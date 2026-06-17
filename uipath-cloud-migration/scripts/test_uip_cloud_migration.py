#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "uip_cloud_migration.py"


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, check=False)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        fixtures = base / "fixtures"
        write_json(fixtures / "source-folders.json", [{"Name": "Finance", "FullyQualifiedName": "Finance"}])
        write_json(fixtures / "target-folders.json", [])
        write_json(fixtures / "source-assets.json", [
            {"Name": "ApiUrl", "Type": "Text", "Value": "https://example.test", "FolderPath": "Finance"},
            {"Name": "DbCred", "Type": "Credential", "Username": "DOMAIN\\svc", "FolderPath": "Finance"}
        ])
        write_json(fixtures / "target-assets.json", [{"Name": "ApiUrl", "Type": "Text", "FolderPath": "Finance"}])
        write_json(fixtures / "source-queues.json", [{"Name": "Invoices", "FolderPath": "Finance", "MaxRetries": 3}])
        write_json(fixtures / "target-queues.json", [])
        write_json(fixtures / "source-packages.json", [{"Id": "InvoiceBot", "Version": "1.0.0"}])
        write_json(fixtures / "target-packages.json", [])
        write_json(fixtures / "source-processes.json", [{"Name": "InvoiceBot", "PackageId": "InvoiceBot", "PackageVersion": "1.0.0", "FolderPath": "Finance"}])
        write_json(fixtures / "target-processes.json", [])
        write_json(fixtures / "source-triggers.json", [{"Name": "InvoiceDaily", "Type": "time", "Cron": "0 0 9 ? * 1-5", "TimeZone": "UTC", "ReleaseKey": "release-key", "FolderPath": "Finance"}])
        write_json(fixtures / "target-triggers.json", [])

        config = base / "migration.json"
        plan = base / "migration-plan.json"
        write_json(config, {
            "source": {"tenant": "Source", "folder_paths": ["Finance"]},
            "target": {"tenant": "Target", "folder_paths": ["Finance"]},
            "entities": ["folders", "assets", "queues", "packages", "processes", "triggers"],
            "package_staging_folder": str(base / "packages"),
            "request_interval_ms": 0,
            "batch_size": 1000,
            "credential_asset_password_mode": "dummy",
            "dummy_credential_password": "DummyPassword",
            "fixture_dir": str(fixtures)
        })

        help_result = run("--help")
        assert help_result.returncode == 0, help_result.stderr

        plan_result = run("plan", "--config", str(config), "--out", str(plan))
        assert plan_result.returncode == 0, plan_result.stderr
        payload = json.loads(plan.read_text(encoding="utf-8"))
        assert [a["entity"] for a in payload["actions"]] == ["folders", "assets", "queues", "packages", "processes", "triggers"]
        assert len(payload["skipped"]) == 1, payload["skipped"]
        assert payload["manual_remediation"], "credential asset remediation should be reported"
        assert payload["actions"][1]["dummy_password"] == "DummyPassword"

        validate_result = run("validate", "--config", str(config), "--plan", str(plan))
        assert validate_result.returncode != 0, "package validation must require staged .nupkg files"

        package_dir = base / "packages"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "InvoiceBot.1.0.0.nupkg").write_bytes(b"fixture package")

        validate_result = run("validate", "--config", str(config), "--plan", str(plan))
        assert validate_result.returncode == 0, validate_result.stderr

        apply_result = run("apply", "--config", str(config), "--plan", str(plan))
        assert apply_result.returncode != 0, "apply must require --yes"

        extended_config = base / "extended-migration.json"
        extended_plan = base / "extended-migration-plan.json"
        write_json(fixtures / "source-credential_stores.json", [{"Name": "DefaultCredentialStore", "Type": "Database"}])
        write_json(fixtures / "target-credential_stores.json", [])
        write_json(fixtures / "source-roles.json", [{"Name": "CustomOperator", "Type": "Folder"}])
        write_json(fixtures / "target-roles.json", [])
        write_json(fixtures / "source-users.json", [{"UserName": "user@example.com", "Name": "User Example"}])
        write_json(fixtures / "target-users.json", [])
        write_json(fixtures / "source-machines.json", [{"Name": "MachineTemplate1"}])
        write_json(fixtures / "target-machines.json", [])
        write_json(fixtures / "source-robots.json", [{"Name": "ClassicRobot1", "Username": "DOMAIN\\robot"}])
        write_json(fixtures / "target-robots.json", [])
        write_json(fixtures / "source-environments.json", [{"Name": "ClassicEnvironment1"}])
        write_json(fixtures / "target-environments.json", [])
        write_json(fixtures / "source-storage_buckets.json", [{"Name": "reports", "FolderPath": "Finance"}])
        write_json(fixtures / "target-storage_buckets.json", [])
        write_json(fixtures / "source-libraries.json", [{"Id": "SharedLibrary", "Version": "1.0.0"}])
        write_json(fixtures / "target-libraries.json", [])
        write_json(fixtures / "source-calendars.json", [{"Name": "BusinessDays", "TimeZoneId": "UTC"}])
        write_json(fixtures / "target-calendars.json", [])
        write_json(fixtures / "source-webhooks.json", [{"Name": "FailureHook", "Url": "https://example.test/hook"}])
        write_json(fixtures / "target-webhooks.json", [])
        write_json(fixtures / "source-feeds.json", [{"Name": "Tenant Feed"}])
        write_json(fixtures / "target-feeds.json", [])
        write_json(fixtures / "source-settings.json", [{"Name": "Abp.Timing.TimeZone", "Value": "UTC"}])
        write_json(fixtures / "target-settings.json", [])
        write_json(extended_config, {
            "source": {"tenant": "Source", "folder_paths": ["Finance"]},
            "target": {"tenant": "Target", "folder_paths": ["Finance"]},
            "entities": [
                "credential_stores", "roles", "users", "machines", "robots", "environments",
                "storage_buckets", "libraries", "calendars", "webhooks", "feeds", "settings"
            ],
            "fixture_dir": str(fixtures),
            "continue_on_entity_error": True,
            "credential_asset_password_mode": "dummy"
        })
        extended_result = run("plan", "--config", str(extended_config), "--out", str(extended_plan))
        assert extended_result.returncode == 0, extended_result.stderr
        extended_payload = json.loads(extended_plan.read_text(encoding="utf-8"))
        assert extended_payload["actions"], "extended entities should produce actions"
        operations = {(action["entity"], action["identity"]): action["operation"] for action in extended_payload["actions"]}
        assert operations[("roles", "Folder/CustomOperator")] == "create"
        assert operations[("users", "user@example.com")] == "manual_review"
        assert operations[("machines", "MachineTemplate1")] == "create"
        assert operations[("storage_buckets", "Finance/reports")] == "create"
        assert operations[("calendars", "BusinessDays")] == "create"
        assert operations[("webhooks", "FailureHook")] == "create"
        assert operations[("credential_stores", "DefaultCredentialStore")] == "manual_review"
        assert operations[("robots", "ClassicRobot1")] == "manual_review"
        assert operations[("environments", "ClassicEnvironment1")] == "manual_review"
        assert operations[("libraries", "SharedLibrary:1.0.0")] == "manual_review"
        assert operations[("feeds", "Tenant Feed")] == "manual_review"
        assert operations[("settings", "Abp.Timing.TimeZone")] == "manual_review"
        assert extended_payload["manual_remediation"], "manual remediation should be reported for extended entities"
        extended_apply = run("apply", "--config", str(extended_config), "--plan", str(extended_plan), "--yes")
        assert extended_apply.returncode != 0, "manual_review plans must not be auto-applied"

    print("Fixture smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
