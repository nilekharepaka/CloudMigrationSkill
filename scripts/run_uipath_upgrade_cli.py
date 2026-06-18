#!/usr/bin/env python3
"""Locate, build, and run UiPath.Upgrade.Cli from a Studio source checkout."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


CLI_PROJECT = Path("Upgrade/UiPath.Upgrade.Cli/UiPath.Upgrade.Cli.csproj")
CLI_SOLUTION = Path("Upgrade/UiPath.Upgrade.sln")
CLI_EXE_NAME = "UiPath.Upgrade.exe"


def is_studio_root(path: Path) -> bool:
    return (path / CLI_PROJECT).is_file()


def find_studio_root(explicit: str | None) -> Path | None:
    candidates: list[Path] = []

    if explicit:
        candidates.append(Path(explicit).expanduser())

    env_root = os.environ.get("UIPATH_STUDIO_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())

    cwd = Path.cwd()
    candidates.extend([cwd, *cwd.parents])
    candidates.append(Path.home() / "Downloads" / "Studio-26.0.180")

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if is_studio_root(resolved):
            return resolved

    return None


def cli_candidates(studio_root: Path, configuration: str) -> list[Path]:
    output = studio_root / "Upgrade" / "Output" / "cli" / configuration
    candidates = [output / CLI_EXE_NAME]

    if output.exists():
        candidates.extend(sorted(output.rglob(CLI_EXE_NAME)))

    return candidates


def locate_cli(cli_path: str | None, studio_root: Path | None, configuration: str) -> Path | None:
    if cli_path:
        candidate = Path(cli_path).expanduser().resolve()
        return candidate if candidate.exists() else None

    env_cli = os.environ.get("UIPATH_UPGRADE_CLI")
    if env_cli:
        candidate = Path(env_cli).expanduser().resolve()
        if candidate.exists():
            return candidate

    if studio_root:
        for candidate in cli_candidates(studio_root, configuration):
            if candidate.exists():
                return candidate.resolve()

    return None


def build_cli(studio_root: Path, configuration: str) -> int:
    dotnet = shutil.which("dotnet")
    if not dotnet:
        print("dotnet was not found on PATH.", file=sys.stderr)
        return 2

    build_target = studio_root / CLI_SOLUTION
    if not build_target.exists():
        build_target = studio_root / CLI_PROJECT

    if platform.system() != "Windows":
        print(
            "Warning: UiPath.Upgrade.Cli targets net8.0-windows and may require a Windows host.",
            file=sys.stderr,
        )

    command = [dotnet, "build", str(build_target), "--configuration", configuration]
    return subprocess.run(command).returncode


def run_cli(cli: Path, cli_args: list[str]) -> int:
    if not cli_args:
        print(str(cli))
        return 0

    if cli.suffix.lower() == ".dll":
        dotnet = shutil.which("dotnet")
        if not dotnet:
            print("dotnet was not found on PATH.", file=sys.stderr)
            return 2
        command = [dotnet, str(cli), *cli_args]
    else:
        if platform.system() != "Windows" and cli.suffix.lower() == ".exe":
            print(
                f"{cli} is a Windows executable. Run it on Windows or provide a compatible CLI path.",
                file=sys.stderr,
            )
            return 2
        command = [str(cli), *cli_args]

    return subprocess.run(command).returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Locate, build, and run UiPath.Upgrade.exe from a Studio checkout."
    )
    parser.add_argument("--studio-root", help="Path to the Studio source root.")
    parser.add_argument("--cli", help="Path to a prebuilt UiPath.Upgrade.exe.")
    parser.add_argument("--configuration", default="Release", help="Build/output configuration.")
    parser.add_argument("--no-build", action="store_true", help="Do not build if the CLI is missing.")
    parser.add_argument("--build", action="store_true", help="Build the CLI before locating it.")
    parser.add_argument("--locate", action="store_true", help="Print the located CLI path and exit.")
    parser.add_argument("cli_args", nargs=argparse.REMAINDER, help="Arguments passed to UiPath.Upgrade.exe after --.")
    args = parser.parse_args(argv)

    if args.cli_args and args.cli_args[0] == "--":
        args.cli_args = args.cli_args[1:]

    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    studio_root = find_studio_root(args.studio_root)

    if args.build:
        if not studio_root:
            print("Could not find a Studio source root. Pass --studio-root.", file=sys.stderr)
            return 2
        build_code = build_cli(studio_root, args.configuration)
        if build_code != 0:
            return build_code

    cli = locate_cli(args.cli, studio_root, args.configuration)

    if not cli and not args.no_build and not args.locate:
        if not studio_root:
            print("Could not find UiPath.Upgrade.exe or a Studio source root.", file=sys.stderr)
            return 2
        build_code = build_cli(studio_root, args.configuration)
        if build_code != 0:
            return build_code
        cli = locate_cli(args.cli, studio_root, args.configuration)

    if args.locate:
        if not cli:
            print("Could not locate UiPath.Upgrade.exe.", file=sys.stderr)
            return 2
        print(str(cli))
        return 0

    if not cli:
        print("Could not locate UiPath.Upgrade.exe.", file=sys.stderr)
        return 2

    return run_cli(cli, args.cli_args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
