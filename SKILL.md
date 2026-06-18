---
name: uipath-activity-migrator
description: UiPath Activity Migrator workflow for using or extending UiPath.Upgrade.Cli to migrate UiPath Studio projects. Use when Codex needs to analyze or migrate UiPath project.json/.xaml projects from Windows-Legacy/Legacy to Windows, convert supported Classic activities to Modern activities, run Activity Migrator/UiPath.Upgrade.exe analyze or upgrade commands, configure migration extensions, inspect SARIF/HTML migration reports, or assess/implement Windows to Cross-platform/Portable migration support.
---

# UiPath Activity Migrator

## Core Rule

Use `UiPath.Upgrade.Cli` as the source of truth. Do not hand-edit `project.json` or XAML as the primary migration path unless the user explicitly asks for a source-code implementation change or the CLI has no supported path and you explain the gap.

Prefer this order:

1. Inspect the project and requested migration mode.
2. Run `analyze` first.
3. Run `upgrade` to a separate output folder.
4. Validate the output project, generated reports, and remaining warnings.

Read [references/uipath-upgrade-cli.md](references/uipath-upgrade-cli.md) when you need command options, source paths, extension names, or pipeline details. Read [references/windows-to-cross-platform.md](references/windows-to-cross-platform.md) before promising or implementing Windows to Cross-platform migration.

## Migration Modes

Use `analyze` for read-only assessment:

```bash
python3 /Users/sai/.codex/skills/uipath-activity-migrator/scripts/run_uipath_upgrade_cli.py \
  --studio-root /path/to/Studio-26.0.180 \
  -- analyze --project-path /path/to/project --output-format sarif --verbose
```

Use `upgrade` for migration, always with an explicit output folder unless the user asks to overwrite:

```bash
python3 /Users/sai/.codex/skills/uipath-activity-migrator/scripts/run_uipath_upgrade_cli.py \
  --studio-root /path/to/Studio-26.0.180 \
  -- upgrade --project-path /path/to/project --output-path /path/to/project_Upgraded --verbose
```

For Classic to Modern activity conversion, keep extensions enabled. To be explicit, pass:

```bash
--enabled-extensions UiAutomationActivities,MailActivities,MicrosoftActivitiesExtension
```

For a repo/folder with multiple projects:

```bash
python3 /Users/sai/.codex/skills/uipath-activity-migrator/scripts/run_uipath_upgrade_cli.py \
  --studio-root /path/to/Studio-26.0.180 \
  -- bulk --command analyze --path /path/to/repository --verbose
```

## Source-Aware Guidance

The checked source has these behaviors:

- Legacy/Windows-Legacy to Windows is implemented by `ProjectFrameworkUpdaterStep`.
- Classic activity migrations are extension-driven; the built-in extension names are `UiAutomationActivities`, `MailActivities`, and `MicrosoftActivitiesExtension`.
- `upgrade` writes to `--output-path`, or `<project>_Upgraded` when no output path is supplied.
- The current checked source does not implement a generic Windows to Cross-platform/Portable framework update. Treat that as unsupported until verified in the target branch or implemented.

## Build and Runtime Constraints

The CLI target is `net8.0-windows` and uses WPF/WindowsDesktop dependencies. Build and run it on Windows with the required .NET SDK and package feeds. On macOS/Linux, use this skill for source inspection and command preparation unless a compatible prebuilt CLI is provided.

The helper script can locate/build/run the CLI, but dependency restore may require network/feed credentials:

```bash
python3 /Users/sai/.codex/skills/uipath-activity-migrator/scripts/run_uipath_upgrade_cli.py \
  --studio-root /path/to/Studio-26.0.180 --locate
```

## Validation

After migration:

1. Inspect `.upgrade` SARIF/HTML reports and summarize errors/warnings.
2. Verify `project.json` target framework and dependency changes in the output project.
3. Inspect changed `.xaml` files for unresolved namespaces/types.
4. Open/build the output project with Studio or supported automation when available.
5. Report unsupported activities and manual follow-ups explicitly.
