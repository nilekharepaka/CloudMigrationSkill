from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent


TARGET = Path(r"C:\Users\nilekha.repaka\.codex\skills\uipath-cloud-migration")


SKILL_MD = r"""---
name: uipath-cloud-migration
description: Migrate UiPath Orchestrator entities from On-Premises or Automation Suite to Automation Cloud. Use for Cloud migration discovery, diffing, planning, validation, and controlled apply of folders, credential stores, users, roles, machines, assets, queues, buckets, packages, libraries, processes, triggers, webhooks, calendars, and legacy assessment entities with uip CLI or direct On-Prem REST.
---

# UiPath Cloud Migration

## Purpose

Use this skill to migrate practical UiPath Orchestrator entities from an On-Premises source tenant to an Automation Cloud target tenant. The helper supports broad discovery and diff planning. Automatic apply remains deliberately conservative and is limited to entity types with implemented safe create/upload commands.

Always load and follow `uipath-platform` before executing commands that touch UiPath Cloud or Orchestrator. Prefer `uip` CLI command families. Use raw REST only after checking `uipath-platform/references/uip-commands.md` and confirming no `uip` command covers the operation.

## Workflow

1. Collect source and target details for this run.
   - Ask for the On-Prem tenant name, Identity URL, Orchestrator URL, and target Automation Cloud tenant.
   - Ask whether the On-Prem source should use `direct_rest` External App auth or `uip` auth.
   - Never save client secrets, user keys, passwords, or access tokens in the skill, examples, repository files, migration plan, or discovery snapshots.
   - Use environment variables for External App credentials, for example `UIP_ONPREM_CLIENT_ID` and `UIP_ONPREM_CLIENT_SECRET`.
2. Create a local, unshared migration config.
   - Prefer `scripts/uip_cloud_migration.py init-config --out migration.local.json`.
   - The interactive setup writes only tenant names, URLs, folder scope, entity choices, and environment variable names.
   - Treat generated `migration.local.json`, discovery JSON, plans, package downloads, and apply outputs as operator-local artifacts; do not commit or share them unless the user explicitly sanitizes them.
   - Use `references/config-example.json` only as a placeholder template.
3. Confirm source and target access:
   - Use `uip login status --output json`.
   - Switch tenants deliberately with `uip login tenant set "<tenant>" --output json` when needed.
   - For Standalone On-Prem where `uip login` does not persist, use `source.auth_mode: "direct_rest"` with External App env-var names.
   - Use `credential_asset_password_mode: "dummy"` for v1 unless the user provides a stronger secret handling process.
4. Run discovery for both sides.
   - Use `scripts/uip_cloud_migration.py discover --config migration.local.json --side source`.
   - Use `scripts/uip_cloud_migration.py discover --config migration.local.json --side target`.
5. Generate the migration plan.
   - Use `scripts/uip_cloud_migration.py plan --config migration.local.json --out migration-plan.json`.
   - For large tenants or offline rehearsals, reuse prior snapshots with `--source-discovery source.json --target-discovery target.json`.
   - Review skipped records, manual remediation, and actions in dependency order.
6. Validate before apply.
   - Use `scripts/uip_cloud_migration.py validate --config migration.local.json --plan migration-plan.json`.
   - Resolve validation errors before creating anything in Cloud.
7. Apply only after explicit user approval.
   - Use `scripts/uip_cloud_migration.py apply --config migration.local.json --plan migration-plan.json --yes`.
   - For On-Prem direct REST targets, start with `--max-actions 1` and inspect the result/target before increasing the limit.
   - Apply consumes an existing plan and refuses to run without it.

## Entity Order

Use this order for every migration plan:

1. `folders`
2. `credential_stores`
3. `roles`
4. `users`
5. `machines`
6. `robots`
7. `environments`
8. `assets`
9. `queues`
10. `storage_buckets`
11. `packages`
12. `libraries`
13. `processes`
14. `calendars`
15. `triggers`
16. `webhooks`
17. `feeds`
18. `settings`

This keeps dependencies stable: folders and tenant primitives first, folder-scoped resources next, packages before processes, and processes/queues/calendars before triggers. Legacy `robots` and `environments` are discovered for migration assessment but usually require Cloud-modernization decisions rather than direct apply.

## Exactness Boundaries

Treat "exact migration" as recreating all migratable Cloud-supported fields and relationships. Do not promise preservation of source IDs, audit metadata, read-only fields, runtime/job history, queue item history, robot session state, license consumption, identity-provider identities, credential store secrets, storage-provider secrets, bucket file contents, or real credential passwords.

Credential and secret values are not returned by ordinary Orchestrator metadata calls. In v1, create credential assets with the configured dummy password and report each credential asset requiring post-migration correction.

Automatic `apply` currently supports safe Cloud-native creation for folders, custom roles, machine templates, assets, queues, built-in storage buckets, packages, processes, calendars, triggers, and webhooks. Direct REST On-Prem target apply supports folders, custom roles, machine templates, assets, queues, packages, processes, and calendars; use canary limits before bulk apply. Identity Service user import is available only when `auto_import_users` is set to `true`. Credential stores, legacy robots/environments, libraries, feeds, settings, external storage buckets, built-in/static roles, and unconfirmed identity principals remain `manual_review`.

## Bundled Resources

- `scripts/uip_cloud_migration.py`: discovery, planning, validation, and controlled apply helper.
- `scripts/test_uip_cloud_migration.py`: fixture-based smoke tests for the helper.
- `references/orchestrator-manager-2.6.2.md`: summarized behavior and limitations from the source Orchestrator Manager project and manual.
- `references/entity-mapping.json`: machine-readable Lift-and-Shift sheet mapping.
- `references/config-example.json`: starter config template.
"""


ORCHESTRATOR_MANAGER_REF = r"""# Orchestrator Manager 2.6.2 Migration Reference

## Source Project

The source project is `OrchestratorManager`, a UiPath Studio Windows project using `Main.xaml`, `LiftAndShift.xaml`, `Orchestrator.xaml`, per-entity workflows under `Entities`, HTTP helpers under `Common` and `HTTP`, and Excel workbooks under `Workbooks`.

The project description says it performs bulk operations for users, robots, assets, machines, environments, processes, organization units, folders, queues, packages, libraries, and triggers. The 2.6 Lift-and-Shift feature is narrower and supports six migration entities. This skill extends discovery and diff planning beyond Lift-and-Shift to additional practical Orchestrator entities such as credential stores, roles, users, machines, storage buckets, libraries, calendars, webhooks, feeds, and settings.

## Lift-and-Shift Scope

The Lift-and-Shift workflow migrates:

- Folders
- Assets
- Queues
- Packages
- Processes
- Triggers

The documented flow is:

1. Input source instance details.
2. Authenticate to the source.
3. Select entities.
4. Run `Get` operations for the selected entities.
5. Copy data from each `Get` sheet to the corresponding create/upload sheet.
6. Input destination instance details.
7. Authenticate to the destination.
8. Run create/upload operations in the destination.

## Entity Mapping

| Entity | Source | Destination | Intermediate |
| --- | --- | --- | --- |
| folder | Get | Create | |
| asset | Get | Create | |
| asset credential | Get Credential | Create Credential | |
| queue | Get | Create | |
| package | Get | Uploadpackagelibrary | Downloadpackagelibrary |
| process | Get | Create | |
| trigger | Get | Create | |

Packages require a download step before upload to the target tenant.

## Config Concepts To Preserve

- `MaximumRequestsThreshold`, `RequestInterval`, and `RequestBatchSize` are safety controls.
- `DownloadedPackagesFolder` or the skill config `package_staging_folder` stores temporary packages.
- `EntitiesToProcess` maps to selected entities in the skill config.
- `EntityMappingFilePath`, `EntityNamesSheetName`, and `EntityMappingsSheetName` become `references/entity-mapping.json`.
- `GetCredentialAssetsViaRobot` is not used in v1; `credential_asset_password_mode: "dummy"` is used instead.

## Extended Discovery Scope

The helper supports broad discovery and plan diffs for:

- Tenant structure: folders, roles, users, machines, credential stores, calendars, feeds, settings.
- Classic/legacy assessment: robots and environments.
- Folder-scoped resources: assets, queues, storage buckets, processes/releases, triggers.
- Package resources: packages and libraries.
- Integration callbacks: webhooks.

Automatic apply is intentionally limited to entities with safe Cloud-native create/upload commands. Custom roles, machine templates, built-in storage buckets, calendars, and webhooks can now be applied automatically; credential stores, legacy robots/environments, libraries, feeds, settings, external storage buckets, built-in/static roles, and unconfirmed identity principals remain manual.

## Important Limitations

- Minimum source Orchestrator version in the original tool is 2018.4.
- Organization Units must be enabled for 2018.4 and 2019.4.
- Users, roles, machines, credential stores, storage buckets, webhooks, calendars, settings, robots, and environments are included in discovery/planning. Safe subsets are automatic; secret-bearing, legacy, built-in/static, or identity-dependent records remain manual.
- Real credential asset passwords cannot be copied through metadata discovery. Dummy credentials must be corrected after migration.
- Credential store provider secrets, storage-provider secrets, bucket file contents, source IDs, audit fields, runtime/job history, queue item history, and read-only server metadata are not migratable as-is.
"""


ENTITY_MAPPING = {
    "version": "orchestrator-manager-2.6.2",
    "dependency_order": [
        "folders",
        "credential_stores",
        "roles",
        "users",
        "machines",
        "robots",
        "environments",
        "assets",
        "queues",
        "storage_buckets",
        "packages",
        "libraries",
        "processes",
        "calendars",
        "triggers",
        "webhooks",
        "feeds",
        "settings",
    ],
    "mappings": [
        {"entity": "folder", "source": "Get", "destination": "Create", "intermediate": None, "range": "A1:E1"},
        {"entity": "asset", "source": "Get", "destination": "Create", "intermediate": None, "range": "A1:G1"},
        {"entity": "asset", "kind": "credential", "source": "Get Credential", "destination": "Create Credential", "intermediate": None, "range": "A1:G1"},
        {"entity": "queue", "source": "Get", "destination": "Create", "intermediate": None, "range": "A1:F1"},
        {"entity": "package", "source": "Get", "destination": "Uploadpackagelibrary", "intermediate": "Downloadpackagelibrary", "range": "A1:F1"},
        {"entity": "process", "source": "Get", "destination": "Create", "intermediate": None, "range": "A1:G1"},
        {"entity": "trigger", "source": "Get", "destination": "Create", "intermediate": None, "range": "A1:U1"},
    ],
}


CONFIG_EXAMPLE = {
    "source": {
        "tenant": "<source-onprem-tenant>",
        "auth_mode": "direct_rest",
        "identity_url": "https://<onprem-host>/identity",
        "orchestrator_url": "https://<onprem-host>",
        "client_id_env": "UIP_ONPREM_CLIENT_ID",
        "client_secret_env": "UIP_ONPREM_CLIENT_SECRET",
        "scope": "OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration OR.Jobs OR.Users OR.Robots OR.Machines OR.Webhooks OR.License",
        "folder_paths": [],
        "uip_extra_args": []
    },
    "target": {
        "tenant": "<target-cloud-tenant>",
        "folder_paths": [],
        "uip_extra_args": []
    },
    "entities": [
        "folders",
        "credential_stores",
        "roles",
        "users",
        "machines",
        "robots",
        "environments",
        "assets",
        "queues",
        "storage_buckets",
        "packages",
        "libraries",
        "processes",
        "calendars",
        "triggers",
        "webhooks",
        "feeds",
        "settings",
    ],
    "package_staging_folder": "DownloadedPackages",
    "request_interval_ms": 250,
    "batch_size": 1000,
    "continue_on_entity_error": True,
    "continue_on_folder_error": True,
    "continue_on_package_error": True,
    "continue_on_apply_error": True,
    "credential_asset_password_mode": "dummy",
    "dummy_credential_password": "DummyPassword",
    "webhook_dummy_secret": "RotateThisWebhookSecret"
}


MIGRATION_SCRIPT = r'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PLAN_VERSION = "uipath-cloud-migration/v1"
ENTITY_ORDER = [
    "folders",
    "credential_stores",
    "roles",
    "users",
    "machines",
    "robots",
    "environments",
    "assets",
    "queues",
    "storage_buckets",
    "packages",
    "libraries",
    "processes",
    "calendars",
    "triggers",
    "webhooks",
    "feeds",
    "settings",
]
APPLY_SUPPORTED_ENTITIES = {
    "folders",
    "roles",
    "users",
    "machines",
    "assets",
    "queues",
    "storage_buckets",
    "packages",
    "processes",
    "calendars",
    "triggers",
    "webhooks",
}
ENTITY_ALIASES = {
    "folder": "folders",
    "folders": "folders",
    "credential_store": "credential_stores",
    "credential_stores": "credential_stores",
    "credential-store": "credential_stores",
    "credential-stores": "credential_stores",
    "credentialstore": "credential_stores",
    "credentialstores": "credential_stores",
    "role": "roles",
    "roles": "roles",
    "user": "users",
    "users": "users",
    "machine": "machines",
    "machines": "machines",
    "machine_template": "machines",
    "machine_templates": "machines",
    "machine-template": "machines",
    "machine-templates": "machines",
    "robot": "robots",
    "robots": "robots",
    "environment": "environments",
    "environments": "environments",
    "asset": "assets",
    "assets": "assets",
    "queue": "queues",
    "queues": "queues",
    "storage_bucket": "storage_buckets",
    "storage_buckets": "storage_buckets",
    "storage-bucket": "storage_buckets",
    "storage-buckets": "storage_buckets",
    "bucket": "storage_buckets",
    "buckets": "storage_buckets",
    "package": "packages",
    "packages": "packages",
    "library": "libraries",
    "libraries": "libraries",
    "process": "processes",
    "processes": "processes",
    "calendar": "calendars",
    "calendars": "calendars",
    "trigger": "triggers",
    "triggers": "triggers",
    "webhook": "webhooks",
    "webhooks": "webhooks",
    "feed": "feeds",
    "feeds": "feeds",
    "setting": "settings",
    "settings": "settings",
}


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def safe_file_part(value: str) -> str:
    cleaned = "".join(char if char not in '<>:"/\\|?*' else "_" for char in str(value))
    return cleaned.strip().strip(".") or "package"


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def canonical_entity(name: str) -> str:
    try:
        return ENTITY_ALIASES[name.strip().lower()]
    except KeyError:
        fail(f"Unsupported entity '{name}'. Supported: {', '.join(ENTITY_ORDER)}")


def selected_entities(config: dict[str, Any]) -> list[str]:
    raw = config.get("entities") or ENTITY_ORDER
    selected: list[str] = []
    for entity in raw:
        canonical = canonical_entity(str(entity))
        if canonical not in selected:
            selected.append(canonical)
    return [entity for entity in ENTITY_ORDER if entity in selected]


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config)
    normalized.setdefault("source", {})
    normalized.setdefault("target", {})
    normalized.setdefault("entities", ENTITY_ORDER)
    normalized.setdefault("package_staging_folder", "DownloadedPackages")
    normalized.setdefault("request_interval_ms", 0)
    normalized.setdefault("batch_size", 1000)
    normalized.setdefault("continue_on_entity_error", True)
    normalized.setdefault("continue_on_apply_error", False)
    normalized.setdefault("credential_asset_password_mode", "dummy")
    normalized.setdefault("dummy_credential_password", "DummyPassword")
    if normalized["credential_asset_password_mode"] != "dummy":
        fail("V1 supports only credential_asset_password_mode='dummy'.")
    return normalized


def prompt_text(label: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value and default:
            value = default
        if value or not required:
            return value
        print("This value is required.")


def prompt_choice(label: str, choices: list[str], default: str) -> str:
    normalized = {choice.lower(): choice for choice in choices}
    while True:
        value = prompt_text(f"{label} ({'/'.join(choices)})", default).lower()
        if value in normalized:
            return normalized[value]
        print(f"Choose one of: {', '.join(choices)}")


def prompt_yes_no(label: str, default: bool = False) -> bool:
    default_text = "Y" if default else "N"
    value = prompt_text(f"{label} (y/n)", default_text).lower()
    return value in {"y", "yes", "true", "1"}


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def default_migration_config() -> dict[str, Any]:
    return {
        "source": {
            "tenant": "",
            "auth_mode": "direct_rest",
            "identity_url": "",
            "orchestrator_url": "",
            "client_id_env": "UIP_ONPREM_CLIENT_ID",
            "client_secret_env": "UIP_ONPREM_CLIENT_SECRET",
            "scope": "OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration OR.Jobs OR.Users OR.Robots OR.Machines OR.Webhooks OR.License",
            "folder_paths": [],
            "uip_extra_args": [],
        },
        "target": {
            "tenant": "",
            "folder_paths": [],
            "uip_extra_args": [],
        },
        "entities": ENTITY_ORDER,
        "package_staging_folder": "DownloadedPackages",
        "request_interval_ms": 250,
        "batch_size": 1000,
        "continue_on_entity_error": True,
        "continue_on_folder_error": True,
        "continue_on_package_error": True,
        "continue_on_apply_error": True,
        "credential_asset_password_mode": "dummy",
        "dummy_credential_password": "DummyPassword",
        "webhook_dummy_secret": "RotateThisWebhookSecret",
    }


def init_config_interactive(out_path: str | Path, overwrite: bool = False) -> dict[str, Any]:
    destination = Path(out_path)
    if destination.exists() and not overwrite:
        fail(f"{destination} already exists. Re-run with --overwrite to replace it.")

    print("UiPath Cloud Migration config setup")
    print("Secrets are not written to this file. Store app IDs/secrets in environment variables.")
    config = default_migration_config()

    source = config["source"]
    source_auth = prompt_choice("Source auth mode", ["direct_rest", "uip"], "direct_rest")
    source["auth_mode"] = source_auth
    source["tenant"] = prompt_text("Source On-Prem tenant name", required=True)
    if source_auth == "direct_rest":
        source["identity_url"] = prompt_text("Source Identity URL", "https://<onprem-host>/identity", required=True).rstrip("/")
        source["orchestrator_url"] = prompt_text("Source Orchestrator URL", "https://<onprem-host>", required=True).rstrip("/")
        source["client_id_env"] = prompt_text("Source client ID environment variable", "UIP_ONPREM_CLIENT_ID", required=True)
        source["client_secret_env"] = prompt_text("Source client secret environment variable", "UIP_ONPREM_CLIENT_SECRET", required=True)
        source["scope"] = prompt_text("Source External App scopes", source["scope"], required=True)
    else:
        source.pop("identity_url", None)
        source.pop("orchestrator_url", None)
        source.pop("client_id_env", None)
        source.pop("client_secret_env", None)
        source.pop("scope", None)

    folder_text = prompt_text("Folder paths to migrate, comma-separated; blank means all discoverable folders")
    source["folder_paths"] = split_csv(folder_text)

    target = config["target"]
    target["tenant"] = prompt_text("Target Automation Cloud tenant name", required=True)
    target_folder_text = prompt_text("Target folder paths, comma-separated; blank reuses source folder scope")
    target["folder_paths"] = split_csv(target_folder_text) if target_folder_text.strip() else source["folder_paths"]

    entity_text = prompt_text("Entities to include, comma-separated; blank means all supported entities")
    if entity_text.strip():
        config["entities"] = [canonical_entity(item) for item in split_csv(entity_text)]

    config["package_staging_folder"] = prompt_text("Package staging folder", "DownloadedPackages", required=True)
    config["request_interval_ms"] = int(prompt_text("Request interval in milliseconds", "250", required=True))
    config["batch_size"] = int(prompt_text("Batch size", "1000", required=True))
    config["continue_on_entity_error"] = prompt_yes_no("Continue when discovery of one entity fails", True)
    config["continue_on_folder_error"] = prompt_yes_no("Continue when one folder-scoped discovery call fails", True)
    config["continue_on_package_error"] = prompt_yes_no("Continue when one package download fails", True)
    config["continue_on_apply_error"] = prompt_yes_no("Continue when one apply action fails", True)
    config["dummy_credential_password"] = prompt_text("Dummy password for credential assets", "DummyPassword", required=True)
    config["webhook_dummy_secret"] = prompt_text("Temporary webhook secret", "RotateThisWebhookSecret", required=True)

    write_json(destination, config)
    print(f"Wrote local migration config: {destination}")
    if source_auth == "direct_rest":
        print("Before running discovery, set these environment variables in your shell:")
        print(f"  $env:{source['client_id_env']} = '<source external app id>'")
        print(f"  $env:{source['client_secret_env']} = '<source external app secret>'")
    print("For the cloud target, log in with `uip login --output json` and select the target tenant.")
    return config


def extract_items(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("Data", "Result", "Items", "items", "value", "results"):
            if key in payload:
                return extract_items(payload[key])
        return [payload]
    return []


def first_value(record: dict[str, Any], keys: list[str], default: str = "") -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def folder_path(record: dict[str, Any]) -> str:
    return first_value(record, ["FullyQualifiedName", "FullyQualifiedPath", "FullPath", "Path", "FolderPath", "Name", "DisplayName"])


def record_name(record: dict[str, Any]) -> str:
    return first_value(record, ["Name", "DisplayName", "Key", "Id", "PackageId"])


def record_folder(record: dict[str, Any]) -> str:
    return first_value(record, ["FolderPath", "Folder", "OrganizationUnitFullyQualifiedName", "OrganizationUnitName", "TenantName"])


def package_id(record: dict[str, Any]) -> str:
    return first_value(record, ["PackageId", "Id", "Name", "Key"])


def package_version(record: dict[str, Any]) -> str:
    return first_value(record, ["Version", "PackageVersion", "ReleaseVersion"])


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def append_if_value(command: list[str], flag: str, value: Any) -> None:
    if value not in (None, ""):
        command.extend([flag, str(value)])


def folder_create_name_parent(record: dict[str, Any]) -> tuple[str, str]:
    path = folder_path(record)
    if "/" in path:
        parent, name = path.rsplit("/", 1)
        return name, parent
    if "\\" in path:
        parent, name = path.rsplit("\\", 1)
        return name, parent
    return record_name(record) or path, first_value(record, ["ParentPath", "ParentFolderPath", "ParentName"])


def role_is_auto_supported(record: dict[str, Any]) -> bool:
    role_type = first_value(record, ["Type", "RoleType"])
    if role_type not in {"Tenant", "Folder"}:
        return False
    if truthy(record.get("IsStatic")):
        return False
    return True


def storage_bucket_is_auto_supported(record: dict[str, Any], config: dict[str, Any]) -> bool:
    provider = first_value(record, ["StorageProvider"])
    if not provider:
        return True
    return truthy(config.get("auto_apply_external_storage_buckets"))


def user_is_auto_supported(record: dict[str, Any], config: dict[str, Any]) -> bool:
    if not truthy(config.get("auto_import_users")):
        return False
    return bool(first_value(record, ["UserName", "Username", "EmailAddress", "Email", "DirectoryIdentifier"]))


def identity(entity: str, record: dict[str, Any]) -> str:
    if entity == "folders":
        return folder_path(record)
    if entity == "credential_stores":
        return first_value(record, ["Name", "StoreName", "Id", "Key"])
    if entity == "roles":
        role_type = first_value(record, ["Type", "RoleType"])
        name = record_name(record)
        return f"{role_type}/{name}" if role_type else name
    if entity == "users":
        return first_value(record, ["UserName", "Username", "EmailAddress", "Email", "Name", "Key", "Id"])
    if entity == "machines":
        return record_name(record)
    if entity == "robots":
        return first_value(record, ["Name", "RobotName", "Username", "MachineName", "Key", "Id"])
    if entity == "environments":
        return record_name(record)
    if entity == "storage_buckets":
        name = record_name(record)
        folder = record_folder(record)
        return f"{folder}/{name}" if folder else name
    if entity == "packages":
        version = package_version(record)
        return f"{package_id(record)}:{version}" if version else package_id(record)
    if entity == "libraries":
        version = package_version(record)
        name = package_id(record) or record_name(record)
        return f"{name}:{version}" if version else name
    if entity == "calendars":
        return record_name(record)
    if entity == "webhooks":
        return first_value(record, ["Name", "Url", "URL", "Key", "Id"])
    if entity == "feeds":
        return record_name(record)
    if entity == "settings":
        return first_value(record, ["Name", "Key", "SettingName", "Id"])
    name = record_name(record)
    folder = record_folder(record)
    return f"{folder}/{name}" if folder else name


def is_credential_asset(record: dict[str, Any]) -> bool:
    probe = " ".join(str(record.get(key, "")) for key in ("Type", "ValueType", "AssetType", "CredentialStoreType"))
    return "credential" in probe.lower()


def fixture_path(config: dict[str, Any], side: str, entity: str) -> Path | None:
    fixture_dir = config.get("fixture_dir")
    if not fixture_dir:
        return None
    base = Path(fixture_dir)
    candidates = [
        base / f"{side}-{entity}.json",
        base / f"{entity}-{side}.json",
        base / side / f"{entity}.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def resolve_command(args: list[str]) -> list[str]:
    if not args or args[0].lower() != "uip":
        return args
    configured = os.environ.get("UIP_CLI_COMMAND")
    if configured:
        return configured.split() + args[1:]
    for name in ("uip", "uip.cmd", "uip.exe"):
        found = shutil.which(name)
        if found:
            return [found, *args[1:]]
    npm_dir = Path.home() / "AppData" / "Roaming" / "npm"
    for candidate in (npm_dir / "uip.cmd", npm_dir / "uip.ps1"):
        if candidate.exists():
            if candidate.suffix.lower() == ".ps1":
                return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(candidate), *args[1:]]
            return [str(candidate), *args[1:]]
    return args


def run_command(args: list[str], *, capture: bool = True) -> Any:
    command = resolve_command(args)
    completed = subprocess.run(command, text=True, capture_output=capture, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() if completed.stderr else ""
        stdout = completed.stdout.strip() if completed.stdout else ""
        fail(f"Command failed ({completed.returncode}): {' '.join(command)}\n{stderr or stdout}")
    if not capture:
        return None
    text = completed.stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def without_option(args: list[str], *names: str) -> list[str]:
    result: list[str] = []
    index = 0
    names_set = set(names)
    while index < len(args):
        if args[index] in names_set:
            index += 2
            continue
        result.append(args[index])
        index += 1
    return result


def with_pagination(args: list[str], limit: int, offset: int) -> list[str]:
    command = without_option(args, "--limit", "-l", "--offset")
    command.extend(["--limit", str(limit), "--offset", str(offset)])
    return command


def run_paginated_command(args: list[str], side_config: dict[str, Any]) -> list[dict[str, Any]]:
    limit = int(side_config.get("batch_size", 1000) or 1000)
    offset = 0
    records: list[dict[str, Any]] = []
    while True:
        payload = run_command(with_pagination(args, limit, offset))
        batch = extract_items(payload)
        records.extend(batch)
        pagination = payload.get("Pagination") if isinstance(payload, dict) else None
        if not isinstance(pagination, dict) or not pagination.get("HasMore"):
            return records
        returned = int(pagination.get("Returned") or len(batch) or 0)
        if returned <= 0:
            return records
        offset += returned


def direct_rest_enabled(side_config: dict[str, Any]) -> bool:
    return side_config.get("auth_mode") == "direct_rest"


def get_direct_rest_token(side_config: dict[str, Any]) -> str:
    cached = side_config.get("_access_token")
    if cached:
        return str(cached)
    identity_url = str(side_config.get("identity_url", "")).rstrip("/")
    if not identity_url:
        fail("direct_rest auth requires identity_url.")
    client_id_env = side_config.get("client_id_env", "UIP_ONPREM_CLIENT_ID")
    client_secret_env = side_config.get("client_secret_env", "UIP_ONPREM_CLIENT_SECRET")
    client_id = os.environ.get(str(client_id_env))
    client_secret = os.environ.get(str(client_secret_env))
    if not client_id:
        fail(f"Environment variable {client_id_env} is not set.")
    if not client_secret:
        fail(f"Environment variable {client_secret_env} is not set.")
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": side_config.get("scope", "OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration OR.Jobs"),
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{identity_url}/connect/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        fail(f"Token request failed ({exc.code}): {details}")
    token = payload.get("access_token")
    if not token:
        fail(f"Token response did not contain access_token: {payload}")
    side_config["_access_token"] = token
    return str(token)


def direct_rest_headers(side_config: dict[str, Any], folder_id: Any | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {get_direct_rest_token(side_config)}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    tenant = side_config.get("tenant")
    if tenant:
        headers["X-UIPATH-TenantName"] = str(tenant)
    if folder_id not in (None, ""):
        headers["X-UIPATH-OrganizationUnitId"] = str(folder_id)
    return headers


def append_query(url: str, params: dict[str, Any]) -> str:
    separator = "&" if "?" in url else "?"
    return url + separator + urllib.parse.urlencode(params)


def direct_rest_get(side_config: dict[str, Any], endpoint: str, folder_id: Any | None = None) -> dict[str, Any]:
    base = str(side_config.get("orchestrator_url", "")).rstrip("/")
    if not base:
        fail("direct_rest auth requires orchestrator_url.")
    request = urllib.request.Request(base + endpoint, headers=direct_rest_headers(side_config, folder_id), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        fail(f"GET {endpoint} failed ({exc.code}): {details}")


def direct_rest_request_json(side_config: dict[str, Any], method: str, endpoint: str, payload: dict[str, Any], folder_id: Any | None = None) -> dict[str, Any]:
    base = str(side_config.get("orchestrator_url", "")).rstrip("/")
    if not base:
        fail("direct_rest auth requires orchestrator_url.")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + endpoint, data=body, headers=direct_rest_headers(side_config, folder_id), method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            text = response.read().decode("utf-8", errors="replace")
            if not text:
                return {"status": response.status}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"status": response.status, "raw": text}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {endpoint} failed ({exc.code}): {details}") from exc


def direct_rest_upload_file(side_config: dict[str, Any], endpoint: str, path: Path, folder_id: Any | None = None) -> dict[str, Any]:
    base = str(side_config.get("orchestrator_url", "")).rstrip("/")
    if not base:
        fail("direct_rest auth requires orchestrator_url.")
    boundary = f"----codex-uipath-migration-{int(time.time() * 1000)}"
    file_bytes = path.read_bytes()
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = header + file_bytes + footer
    headers = direct_rest_headers(side_config, folder_id)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    request = urllib.request.Request(base + endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            text = response.read().decode("utf-8", errors="replace")
            if not text:
                return {"status": response.status}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"status": response.status, "raw": text}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {endpoint} upload failed ({exc.code}): {details}") from exc


def direct_rest_download_file(side_config: dict[str, Any], endpoint: str, destination: Path, folder_id: Any | None = None) -> None:
    base = str(side_config.get("orchestrator_url", "")).rstrip("/")
    if not base:
        fail("direct_rest auth requires orchestrator_url.")
    headers = direct_rest_headers(side_config, folder_id)
    headers["Accept"] = "application/octet-stream"
    headers.pop("Content-Type", None)
    request = urllib.request.Request(base + endpoint, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        fail(f"GET {endpoint} failed ({exc.code}): {details}")


def direct_rest_get_all(side_config: dict[str, Any], endpoint: str, folder_id: Any | None = None) -> list[dict[str, Any]]:
    top = int(side_config.get("batch_size", 1000) or 1000)
    skip = 0
    records: list[dict[str, Any]] = []
    while True:
        payload = direct_rest_get(side_config, append_query(endpoint, {"$top": top, "$skip": skip}), folder_id)
        batch = extract_items(payload)
        records.extend(batch)
        if len(batch) < top:
            return records
        skip += top


def direct_rest_folder_label(record: dict[str, Any]) -> str:
    return first_value(record, ["FullyQualifiedName", "FullyQualifiedPath", "FullPath", "DisplayName", "Name"])


def direct_rest_folder_id(record: dict[str, Any]) -> Any:
    return record.get("Id") or record.get("ID") or record.get("Key")


def direct_rest_folders_for_scope(side_config: dict[str, Any]) -> list[dict[str, Any]]:
    folders = direct_rest_get_all(side_config, "/odata/Folders?$orderby=FullyQualifiedName")
    requested = set(str(item) for item in side_config.get("folder_paths", []) if str(item))
    if not requested:
        return folders
    return [folder for folder in folders if direct_rest_folder_label(folder) in requested or first_value(folder, ["Name"]) in requested]


def direct_rest_discover_entity(config: dict[str, Any], side: str, entity: str) -> list[dict[str, Any]]:
    side_config = config.get(side, {})
    side_config.setdefault("batch_size", config.get("batch_size", 1000))
    if entity == "folders":
        return direct_rest_get_all(side_config, "/odata/Folders?$orderby=FullyQualifiedName")
    tenant_endpoint_by_entity = {
        "credential_stores": "/odata/CredentialStores?$orderby=Name",
        "roles": "/odata/Roles?$orderby=Name",
        "users": "/odata/Users?$orderby=UserName",
        "machines": "/odata/Machines?$orderby=Name",
        "calendars": "/odata/Calendars?$orderby=Name",
        "webhooks": "/odata/Webhooks?$orderby=Name",
        "feeds": "/odata/Feeds?$orderby=Name",
        "settings": "/odata/Settings",
    }
    if entity in tenant_endpoint_by_entity:
        return direct_rest_get_all(side_config, tenant_endpoint_by_entity[entity])
    if entity == "packages":
        records = direct_rest_get_all(side_config, "/odata/Processes?$orderby=Key")
        for record in records:
            record.setdefault("FolderPath", record.get("FolderName") or record.get("TenantName") or "")
        return records
    if entity == "libraries":
        records = direct_rest_get_all(side_config, "/odata/Libraries?$orderby=Id")
        for record in records:
            record.setdefault("FolderPath", record.get("FolderName") or record.get("TenantName") or "")
        return records
    endpoint_by_entity = {
        "assets": "/odata/Assets?$orderby=Name",
        "queues": "/odata/QueueDefinitions?$orderby=Name",
        "robots": "/odata/Robots?$orderby=Name",
        "environments": "/odata/Environments?$orderby=Name",
        "storage_buckets": "/odata/Buckets?$orderby=Name",
        "processes": "/odata/Releases?$expand=ReleaseVersions&$orderby=Name",
        "triggers": "/odata/ProcessSchedules?$orderby=Name",
    }
    endpoint = endpoint_by_entity.get(entity)
    if not endpoint:
        fail(f"No direct_rest endpoint registered for {entity}")
    records: list[dict[str, Any]] = []
    for folder in direct_rest_folders_for_scope(side_config):
        folder_id = direct_rest_folder_id(folder)
        folder_name = direct_rest_folder_label(folder)
        for item in direct_rest_get_all(side_config, endpoint, folder_id):
            item.setdefault("FolderPath", folder_name)
            item.setdefault("OrganizationUnitId", folder_id)
            records.append(item)
    return records


DIRECT_REST_APPLY_SUPPORTED_ENTITIES = {
    "folders",
    "roles",
    "machines",
    "assets",
    "queues",
    "packages",
    "processes",
    "calendars",
}


def as_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_bool(value: Any, default: bool | None = None) -> bool | None:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return truthy(value)


def direct_rest_result_id(payload: dict[str, Any]) -> Any:
    if not isinstance(payload, dict):
        return None
    for key in ("Id", "ID", "Key"):
        if payload.get(key) not in (None, ""):
            return payload.get(key)
    for key in ("Data", "Result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_id = direct_rest_result_id(nested)
            if nested_id not in (None, ""):
                return nested_id
    return None


def direct_rest_identity_index(config: dict[str, Any], entity: str) -> dict[str, dict[str, Any]]:
    try:
        return build_indexes(direct_rest_discover_entity(config, "target", entity), entity)
    except SystemExit:
        raise
    except Exception:
        return {}


def direct_rest_folder_indexes(config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    records = direct_rest_discover_entity(config, "target", "folders")
    by_identity = build_indexes(records, "folders")
    by_label: dict[str, Any] = {}
    for record in records:
        folder_id = direct_rest_folder_id(record)
        for label in {identity("folders", record), direct_rest_folder_label(record), first_value(record, ["Name", "DisplayName"])}:
            if label:
                by_label[str(label)] = folder_id
    return by_identity, by_label


def folder_parent_path(record: dict[str, Any]) -> str:
    path = folder_path(record)
    if "/" in path:
        return path.rsplit("/", 1)[0]
    if "\\" in path:
        return path.rsplit("\\", 1)[0]
    return first_value(record, ["ParentPath", "ParentFolderPath", "ParentName"])


def direct_rest_target_folder_id(state: dict[str, Any], folder: str) -> Any | None:
    if not folder:
        return None
    return state.setdefault("folder_ids", {}).get(folder)


def direct_rest_build_folder_body(record: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    folder_name, _ = folder_create_name_parent(record)
    body: dict[str, Any] = {
        "DisplayName": folder_name or record_name(record) or folder_path(record),
        "ProvisionType": first_value(record, ["ProvisionType"], "Automatic"),
        "PermissionModel": first_value(record, ["PermissionModel"], "FineGrained"),
    }
    parent = folder_parent_path(record)
    parent_id = direct_rest_target_folder_id(state, parent)
    if parent:
        if parent_id in (None, ""):
            raise RuntimeError(f"Parent folder is not available in target yet: {parent}")
        body["ParentId"] = parent_id
    feed_type = first_value(record, ["FeedType"])
    if feed_type and not parent:
        body["FeedType"] = feed_type
    return body


def direct_rest_build_role_body(record: dict[str, Any]) -> dict[str, Any]:
    body = {
        "Name": record_name(record),
        "Type": first_value(record, ["Type", "RoleType"], "Folder"),
    }
    permissions = record.get("Permissions")
    if isinstance(permissions, list):
        body["Permissions"] = permissions
    return body


def direct_rest_build_machine_body(record: dict[str, Any]) -> dict[str, Any]:
    if first_value(record, ["Scope"]).lower() == "personalworkspace":
        raise RuntimeError("Personal workspace machines are not recreated automatically.")
    body: dict[str, Any] = {
        "Name": record_name(record),
        "Type": first_value(record, ["Type"], "Template"),
        "UnattendedSlots": as_int(first_value(record, ["UnattendedSlots", "UnattendedRobotSlots"]), 0),
        "NonProductionSlots": as_int(first_value(record, ["NonProductionSlots"]), 0),
        "TestAutomationSlots": as_int(first_value(record, ["TestAutomationSlots", "TestingSlots"]), 0),
    }
    for key in ("Description", "LicenseKey"):
        value = first_value(record, [key])
        if value:
            body[key] = value
    headless = as_int(first_value(record, ["HeadlessSlots"]), None)
    if headless is not None:
        body["HeadlessSlots"] = headless
    return body


def direct_rest_build_asset_body(config: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    asset_type = first_value(record, ["ValueType", "Type", "AssetType"], "Text")
    body: dict[str, Any] = {
        "Name": record_name(record),
        "ValueType": asset_type,
        "ValueScope": first_value(record, ["ValueScope"], "Global"),
        "HasDefaultValue": True,
    }
    description = first_value(record, ["Description"])
    if description:
        body["Description"] = description
    if is_credential_asset(record):
        body["ValueType"] = "Credential"
        body["CredentialUsername"] = first_value(record, ["CredentialUsername", "Username"], "dummy-user")
        body["CredentialPassword"] = config.get("dummy_credential_password", "DummyPassword")
        return body
    lowered = asset_type.lower()
    value = first_value(record, ["Value", "StringValue", "BoolValue", "IntValue"])
    if lowered in {"bool", "boolean"}:
        body["BoolValue"] = coerce_bool(value, False)
    elif lowered in {"integer", "int32", "int"}:
        body["IntValue"] = as_int(value, 0)
    else:
        body["StringValue"] = value
    return body


def direct_rest_build_queue_body(record: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Name": record_name(record),
        "AcceptAutomaticallyRetry": coerce_bool(record.get("AcceptAutomaticallyRetry"), True),
        "MaxNumberOfRetries": as_int(first_value(record, ["MaxNumberOfRetries", "MaxRetries"]), 0),
        "EnforceUniqueReference": coerce_bool(record.get("EnforceUniqueReference"), False),
    }
    for key in ("Description", "SpecificDataJsonSchema", "OutputDataJsonSchema", "AnalyticsDataJsonSchema"):
        value = first_value(record, [key])
        if value:
            body[key] = value
    for key in ("SlaInMinutes", "RiskSlaInMinutes", "ReleaseId"):
        value = as_int(first_value(record, [key]), None)
        if value is not None:
            body[key] = value
    return body


def direct_rest_build_process_body(record: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Name": record_name(record),
        "ProcessKey": first_value(record, ["ProcessKey", "PackageId", "PackageKey"]),
        "ProcessVersion": first_value(record, ["ProcessVersion", "PackageVersion", "Version"]),
    }
    for key in ("Description", "InputArguments"):
        value = first_value(record, [key])
        if value:
            body[key] = value
    environment_id = as_int(first_value(record, ["EnvironmentId"]), None)
    if environment_id is not None:
        body["EnvironmentId"] = environment_id
    return body


def direct_rest_build_calendar_body(record: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Name": record_name(record),
        "TimeZoneId": first_value(record, ["TimeZoneId", "TimezoneID", "TimeZone"], "UTC") or "UTC",
    }
    excluded = record.get("ExcludedDates")
    if isinstance(excluded, list):
        body["ExcludedDates"] = excluded
    elif excluded:
        body["ExcludedDates"] = [item.strip() for item in str(excluded).split(",") if item.strip()]
    return body


def direct_rest_entity_folder_id(action: dict[str, Any], state: dict[str, Any]) -> Any | None:
    folder = record_folder(action.get("source_record", {}))
    folder_id = direct_rest_target_folder_id(state, folder)
    if folder and folder_id in (None, ""):
        raise RuntimeError(f"Target folder for action is not available: {folder}")
    return folder_id


def direct_rest_apply_one(config: dict[str, Any], action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    entity = action.get("entity")
    record = action.get("source_record", {})
    side_config = config.get("target", {})
    existing = state.setdefault("indexes", {}).setdefault(entity, {})
    identity_key = action.get("identity")
    if identity_key in existing:
        return {"identity": identity_key, "entity": entity, "status": "already_exists"}
    if entity not in DIRECT_REST_APPLY_SUPPORTED_ENTITIES:
        raise RuntimeError(f"direct_rest apply is not implemented for {entity}")
    if entity == "folders":
        payload = direct_rest_request_json(side_config, "POST", "/odata/Folders", direct_rest_build_folder_body(record, state))
        new_id = direct_rest_result_id(payload)
        if new_id not in (None, ""):
            state.setdefault("folder_ids", {})[str(identity_key)] = new_id
            state.setdefault("folder_ids", {})[folder_path(record)] = new_id
            state.setdefault("folder_ids", {})[record_name(record)] = new_id
        existing[str(identity_key)] = record
        return {"identity": identity_key, "entity": entity, "status": "created", "response": payload}
    if entity == "roles":
        payload = direct_rest_request_json(side_config, "POST", "/odata/Roles", direct_rest_build_role_body(record))
        existing[str(identity_key)] = record
        return {"identity": identity_key, "entity": entity, "status": "created", "response": payload}
    if entity == "machines":
        payload = direct_rest_request_json(side_config, "POST", "/odata/Machines", direct_rest_build_machine_body(record))
        existing[str(identity_key)] = record
        return {"identity": identity_key, "entity": entity, "status": "created", "response": payload}
    if entity == "assets":
        folder_id = direct_rest_entity_folder_id(action, state)
        payload = direct_rest_request_json(side_config, "POST", "/odata/Assets", direct_rest_build_asset_body(config, record), folder_id)
        existing[str(identity_key)] = record
        return {"identity": identity_key, "entity": entity, "status": "created", "response": payload}
    if entity == "queues":
        folder_id = direct_rest_entity_folder_id(action, state)
        payload = direct_rest_request_json(side_config, "POST", "/odata/QueueDefinitions", direct_rest_build_queue_body(record), folder_id)
        existing[str(identity_key)] = record
        return {"identity": identity_key, "entity": entity, "status": "created", "response": payload}
    if entity == "packages":
        package_path = package_file_path(config, record)
        payload = direct_rest_upload_file(side_config, "/odata/Processes/UiPath.Server.Configuration.OData.UploadPackage", package_path)
        existing[str(identity_key)] = record
        return {"identity": identity_key, "entity": entity, "status": "uploaded", "path": str(package_path), "response": payload}
    if entity == "processes":
        folder_id = direct_rest_entity_folder_id(action, state)
        payload = direct_rest_request_json(side_config, "POST", "/odata/Releases", direct_rest_build_process_body(record), folder_id)
        existing[str(identity_key)] = record
        return {"identity": identity_key, "entity": entity, "status": "created", "response": payload}
    if entity == "calendars":
        payload = direct_rest_request_json(side_config, "POST", "/odata/Calendars", direct_rest_build_calendar_body(record))
        existing[str(identity_key)] = record
        return {"identity": identity_key, "entity": entity, "status": "created", "response": payload}
    raise RuntimeError(f"direct_rest apply is not implemented for {entity}")


def apply_plan_direct_rest(config: dict[str, Any], plan: dict[str, Any], max_actions: int | None = None) -> dict[str, Any]:
    side_config = config.get("target", {})
    side_config.setdefault("batch_size", config.get("batch_size", 1000))
    folder_index, folder_ids = direct_rest_folder_indexes(config)
    state: dict[str, Any] = {
        "folder_ids": folder_ids,
        "indexes": {"folders": folder_index},
    }
    for entity in {action.get("entity") for action in plan.get("actions", []) if action.get("entity")} - {"folders"}:
        if entity in DIRECT_REST_APPLY_SUPPORTED_ENTITIES:
            state["indexes"][entity] = direct_rest_identity_index(config, entity)
    results = {
        "applied_at": now_utc(),
        "target_mode": "direct_rest",
        "actions": [],
        "errors": [],
        "manual_remediation": plan.get("manual_remediation", []),
    }
    actions = plan.get("actions", [])
    if max_actions is not None:
        actions = actions[:max_actions]
    continue_on_error = truthy(config.get("continue_on_apply_error"))
    for index, action in enumerate(actions, start=1):
        try:
            result = direct_rest_apply_one(config, action, state)
            result["index"] = index
            results["actions"].append(result)
        except Exception as exc:
            error = {
                "index": index,
                "identity": action.get("identity"),
                "entity": action.get("entity"),
                "error": str(exc),
            }
            results["errors"].append(error)
            if not continue_on_error:
                fail(f"direct_rest apply failed for {action.get('entity')}:{action.get('identity')}: {exc}")
        interval = int(config.get("request_interval_ms", 0) or 0)
        if interval > 0:
            time.sleep(interval / 1000)
    return results


def maybe_switch_tenant(config: dict[str, Any], side: str) -> None:
    side_config = config.get(side, {})
    if direct_rest_enabled(side_config):
        return
    tenant = side_config.get("tenant")
    if tenant and not config.get("fixture_dir"):
        run_command(["uip", "login", "tenant", "set", str(tenant), "--output", "json"])


def list_command(entity: str, side_config: dict[str, Any], folder: str | None = None) -> list[str]:
    extra = [str(arg) for arg in side_config.get("uip_extra_args", [])]
    if entity == "folders":
        return ["uip", "or", "folders", "list", "--all", "--output", "json", *extra]
    if entity == "credential_stores":
        return ["uip", "or", "credential-stores", "list", "--output", "json", *extra]
    if entity == "roles":
        return ["uip", "or", "roles", "list", "--output", "json", *extra]
    if entity == "users":
        return ["uip", "or", "users", "list", "--output", "json", *extra]
    if entity == "machines":
        return ["uip", "or", "machines", "list", "--output", "json", *extra]
    if entity == "calendars":
        return ["uip", "or", "calendars", "list", "--output", "json", *extra]
    if entity == "feeds":
        return ["uip", "or", "feeds", "list", "--output", "json", *extra]
    if entity == "settings":
        return ["uip", "or", "settings", "list", "--output", "json", *extra]
    if entity == "libraries":
        return ["uip", "resource", "libraries", "list", "--limit", str(side_config.get("batch_size", 1000)), "--output", "json", *extra]
    if entity == "webhooks":
        return ["uip", "resource", "webhooks", "list", "--output", "json", *extra]
    if entity == "storage_buckets":
        return ["uip", "resource", "buckets", "list", "--all-folders", "--output", "json", *extra]
    if entity == "assets":
        command = ["uip", "resource", "assets", "list", "--output", "json", *extra]
    elif entity == "queues":
        command = ["uip", "resource", "queues", "list", "--output", "json", *extra]
    elif entity == "packages":
        return ["uip", "or", "packages", "list", "--output", "json", *extra]
    elif entity == "processes":
        command = ["uip", "or", "processes", "list", "--output", "json", *extra]
    elif entity == "triggers":
        command = ["uip", "resource", "triggers", "list", "--folder-path", folder or "", "--output", "json", *extra]
        return [part for part in command if part != ""]
    elif entity in {"robots", "environments"}:
        return []
    else:
        fail(f"No list command registered for {entity}")
    if folder:
        command.extend(["--folder-path", folder])
    return command


def discover_entity(config: dict[str, Any], side: str, entity: str, known_folders: list[str]) -> list[dict[str, Any]]:
    fixture = fixture_path(config, side, entity)
    if fixture:
        return extract_items(read_json(fixture))

    side_config = config.get(side, {})
    if direct_rest_enabled(side_config):
        return direct_rest_discover_entity(config, side, entity)

    if entity in {"assets", "queues", "processes", "triggers"}:
        folders = side_config.get("folder_paths") or known_folders
        if not folders:
            fail(f"Discovery for {entity} requires folder_paths or discovered folders.")
        records: list[dict[str, Any]] = []
        for folder in folders:
            command = list_command(entity, side_config, folder)
            if not command:
                return []
            try:
                items = run_paginated_command(command, side_config)
            except SystemExit:
                if not config.get("continue_on_folder_error", True):
                    raise
                continue
            for item in items:
                item.setdefault("FolderPath", folder)
                records.append(item)
        return records

    command = list_command(entity, side_config)
    if not command:
        return []
    return run_paginated_command(command, side_config)


def discover(config: dict[str, Any], side: str) -> dict[str, Any]:
    if side not in {"source", "target"}:
        fail("--side must be source or target")
    maybe_switch_tenant(config, side)
    results: dict[str, Any] = {"side": side, "generated_at": now_utc(), "entities": {}, "errors": []}
    known_folders: list[str] = []
    for entity in selected_entities(config):
        try:
            records = discover_entity(config, side, entity, known_folders)
        except SystemExit as exc:
            if not config.get("continue_on_entity_error", True):
                raise
            records = []
            results["errors"].append({
                "entity": entity,
                "side": side,
                "message": "Discovery failed for this entity. Re-run with only this entity for full stderr details.",
                "code": exc.code,
            })
        results["entities"][entity] = records
        if entity == "folders":
            known_folders = [folder_path(item) for item in records if folder_path(item)]
        interval = int(config.get("request_interval_ms", 0) or 0)
        if interval > 0:
            time.sleep(interval / 1000)
    return results


def package_source_key(record: dict[str, Any]) -> str:
    return first_value(record, ["Key"]) or identity("packages", record)


def package_file_path(config: dict[str, Any], record: dict[str, Any]) -> Path:
    staging = Path(config.get("package_staging_folder", "DownloadedPackages"))
    name = safe_file_part(package_id(record) or record_name(record) or "package")
    version = safe_file_part(package_version(record) or "unknown")
    return staging / f"{name}.{version}.nupkg"


def package_download_endpoint(config: dict[str, Any], record: dict[str, Any]) -> str:
    key = package_source_key(record)
    if not key:
        fail(f"Package record is missing a package key/id: {record}")
    escaped_key = key.replace("'", "''")
    endpoint = f"/odata/Processes/UiPath.Server.Configuration.OData.DownloadPackage(key='{escaped_key}')"
    source_config = config.get("source", {})
    feed_id = first_value(record, ["FeedId", "FeedID", "feedId"]) or source_config.get("package_feed_id") or config.get("package_feed_id")
    if feed_id not in (None, ""):
        endpoint += "?" + urllib.parse.urlencode({"feedId": str(feed_id)})
    return endpoint


def load_package_records(config: dict[str, Any], discovery_path: str | None) -> list[dict[str, Any]]:
    if discovery_path:
        discovery = read_json(discovery_path)
    else:
        discovery = discover(config, "source")
    return list(discovery.get("entities", {}).get("packages", []))


def stage_packages(config: dict[str, Any], discovery_path: str | None = None) -> dict[str, Any]:
    packages = load_package_records(config, discovery_path)
    source_config = config.get("source", {})
    report: dict[str, Any] = {
        "staged_at": now_utc(),
        "package_staging_folder": config.get("package_staging_folder", "DownloadedPackages"),
        "packages": [],
        "failed_packages": [],
    }
    if not packages:
        return report

    if direct_rest_enabled(source_config):
        for record in packages:
            destination = package_file_path(config, record)
            endpoint = package_download_endpoint(config, record)
            if destination.exists() and destination.stat().st_size > 0:
                report["packages"].append({
                    "identity": identity("packages", record),
                    "source_key": package_source_key(record),
                    "path": str(destination),
                    "bytes": destination.stat().st_size,
                    "status": "already_staged",
                })
                continue
            try:
                direct_rest_download_file(source_config, endpoint, destination)
            except SystemExit as exc:
                if not config.get("continue_on_package_error", True):
                    raise
                report["failed_packages"].append({
                    "identity": identity("packages", record),
                    "source_key": package_source_key(record),
                    "path": str(destination),
                    "error": str(exc),
                })
                continue
            report["packages"].append({
                "identity": identity("packages", record),
                "source_key": package_source_key(record),
                "path": str(destination),
                "bytes": destination.stat().st_size if destination.exists() else 0,
            })
        return report

    maybe_switch_tenant(config, "source")
    for record in packages:
        destination = package_file_path(config, record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        key = identity("packages", record)
        if destination.exists() and destination.stat().st_size > 0:
            report["packages"].append({
                "identity": key,
                "source_key": package_source_key(record),
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "status": "already_staged",
            })
            continue
        try:
            run_command(["uip", "or", "packages", "download", key, "--destination", str(destination), "--output", "json"], capture=True)
        except SystemExit as exc:
            if not config.get("continue_on_package_error", True):
                raise
            report["failed_packages"].append({
                "identity": key,
                "source_key": package_source_key(record),
                "path": str(destination),
                "error": str(exc),
            })
            continue
        report["packages"].append({
            "identity": key,
            "source_key": package_source_key(record),
            "path": str(destination),
            "bytes": destination.stat().st_size if destination.exists() else 0,
        })
    return report


def build_indexes(records: list[dict[str, Any]], entity: str) -> dict[str, dict[str, Any]]:
    result = {}
    for record in records:
        key = identity(entity, record)
        if key:
            result[key] = record
    return result


def source_to_target_action(config: dict[str, Any], entity: str, key: str, record: dict[str, Any]) -> dict[str, Any]:
    operation = "create" if entity in APPLY_SUPPORTED_ENTITIES else "manual_review"
    if entity == "roles" and not role_is_auto_supported(record):
        operation = "manual_review"
    if entity == "users" and not user_is_auto_supported(record, config):
        operation = "manual_review"
    if entity == "storage_buckets" and not storage_bucket_is_auto_supported(record, config):
        operation = "manual_review"
    action = {
        "entity": entity,
        "identity": key,
        "operation": operation,
        "source_record": record,
        "uip_family": uip_family(entity),
        "requires_manual_mapping": operation == "manual_review",
        "notes": [],
    }
    if action["operation"] == "manual_review":
        action["notes"].append("Discovery and diff are supported, but automatic apply is not implemented for this entity. Review Cloud mapping, identity dependencies, permissions, and secrets before migration.")
    if entity == "credential_stores":
        action["notes"].append("Credential stores require manual Cloud setup because provider secrets and protected configuration are not returned by Orchestrator metadata APIs.")
    if entity == "roles" and action["operation"] == "manual_review":
        action["notes"].append("Built-in/static or Mixed roles are not recreated. Custom Tenant/Folder roles can be created automatically; permissions require review if not present in discovery.")
    if entity == "users" and action["operation"] == "manual_review":
        action["notes"].append("User migration uses Identity Service import and is disabled until config.auto_import_users is true. Local On-Prem users cannot be copied as local Cloud users.")
    if entity == "storage_buckets" and action["operation"] == "manual_review":
        action["notes"].append("External storage buckets need provider secrets/credential-store mappings. Built-in Orchestrator buckets can be created automatically.")
    if entity == "packages":
        action["operation"] = "download_upload"
        action["package_staging_folder"] = config.get("package_staging_folder", "DownloadedPackages")
    if entity == "assets" and is_credential_asset(record):
        action["credential_asset_password_mode"] = "dummy"
        action["dummy_password"] = config.get("dummy_credential_password", "DummyPassword")
        action["notes"].append("Credential asset will be created with dummy password and must be corrected after migration.")
    if entity == "triggers":
        action["notes"].append("Verify release-key, queue-key, calendar-key, runtime type, and trigger type against target tenant before apply.")
    if entity == "webhooks":
        action["notes"].append("Webhook signing secrets are not returned by discovery. Set config.webhook_dummy_secret to create with a temporary secret, then rotate it in Cloud.")
    return action


def uip_family(entity: str) -> str:
    return {
        "folders": "uip or folders",
        "credential_stores": "uip or credential-stores",
        "roles": "uip or roles",
        "users": "uip or users",
        "machines": "uip or machines",
        "robots": "legacy / direct REST only",
        "environments": "legacy / direct REST only",
        "assets": "uip resource assets",
        "queues": "uip resource queues",
        "storage_buckets": "uip resource buckets",
        "packages": "uip or packages",
        "libraries": "uip resource libraries",
        "processes": "uip or processes",
        "calendars": "uip or calendars",
        "triggers": "uip resource triggers",
        "webhooks": "uip resource webhooks",
        "feeds": "uip or feeds",
        "settings": "uip or settings",
    }[entity]


def make_plan(config: dict[str, Any], source_discovery: str | None = None, target_discovery: str | None = None) -> dict[str, Any]:
    source = read_json(source_discovery) if source_discovery else discover(config, "source")
    target = read_json(target_discovery) if target_discovery else discover(config, "target")
    actions = []
    skipped = []
    manual = []
    for entity in selected_entities(config):
        source_index = build_indexes(source["entities"].get(entity, []), entity)
        target_index = build_indexes(target["entities"].get(entity, []), entity)
        for key, record in sorted(source_index.items()):
            if entity == "roles" and truthy(record.get("IsStatic")):
                skipped.append({"entity": entity, "identity": key, "reason": "built_in_static_role"})
                continue
            if key in target_index:
                skipped.append({"entity": entity, "identity": key, "reason": "already_exists"})
                continue
            action = source_to_target_action(config, entity, key, record)
            actions.append(action)
            if action.get("operation") == "manual_review":
                manual.append({
                    "entity": entity,
                    "identity": key,
                    "reason": "automatic_apply_not_implemented",
                    "required_action": "Review target Cloud equivalent, identity dependencies, permissions, licenses, and secrets before migration.",
                })
            if entity == "assets" and is_credential_asset(record):
                manual.append({
                    "entity": "assets",
                    "identity": key,
                    "reason": "credential_asset_dummy_password",
                    "required_action": "Replace dummy password in target credential asset after migration.",
                })
    return {
        "migration_plan_version": PLAN_VERSION,
        "generated_at": now_utc(),
        "entities": selected_entities(config),
        "dependency_order": ENTITY_ORDER,
        "actions": actions,
        "skipped": skipped,
        "manual_remediation": manual,
        "source_summary": {entity: len(source["entities"].get(entity, [])) for entity in selected_entities(config)},
        "target_summary": {entity: len(target["entities"].get(entity, [])) for entity in selected_entities(config)},
    }


def action_command(action: dict[str, Any], config: dict[str, Any]) -> list[list[str]]:
    record = action["source_record"]
    entity = action["entity"]
    folder = record_folder(record)
    name = record_name(record)
    if entity == "folders":
        folder_name, parent = folder_create_name_parent(record)
        command = ["uip", "or", "folders", "create", folder_name or identity(entity, record), "--output", "json"]
        description = first_value(record, ["Description"])
        if parent:
            command.extend(["--parent", parent])
        if description:
            command.extend(["--description", description])
        return [command]
    if entity == "roles":
        role_type = first_value(record, ["Type", "RoleType"], "Folder")
        return [["uip", "or", "roles", "create", "--name", name, "--type", role_type, "--output", "json"]]
    if entity == "users":
        username = first_value(record, ["UserName", "Username", "EmailAddress", "Email"])
        directory_id = first_value(record, ["DirectoryIdentifier", "DirectoryId"])
        principal_type = first_value(record, ["Type"], "DirectoryUser")
        command = ["uip", "or", "users", "import", "--type", principal_type, "--output", "json"]
        if directory_id:
            command.extend(["--directory-id", directory_id])
        else:
            command.extend(["--username", username])
        append_if_value(command, "--domain", first_value(record, ["Domain"]))
        return [command]
    if entity == "machines":
        command = ["uip", "or", "machines", "create", "--name", name, "--output", "json"]
        append_if_value(command, "--description", first_value(record, ["Description"]))
        if first_value(record, ["Type"]).lower() == "serverless" or truthy(record.get("Serverless")):
            command.append("--serverless")
        slot_map = [
            ("--unattended-slots", ["UnattendedSlots", "UnattendedRobotSlots"]),
            ("--headless-slots", ["HeadlessSlots"]),
            ("--non-production-slots", ["NonProductionSlots"]),
            ("--testing-slots", ["TestAutomationSlots", "TestingSlots"]),
        ]
        for flag, keys in slot_map:
            append_if_value(command, flag, first_value(record, keys))
        return [command]
    if entity == "assets":
        asset_type = first_value(record, ["Type", "ValueType", "AssetType"], "Text")
        value = first_value(record, ["Value", "StringValue", "BoolValue", "IntValue"], "")
        if is_credential_asset(record):
            username = first_value(record, ["Username", "CredentialUsername"], "dummy-user")
            value = f"{username}:{config.get('dummy_credential_password', 'DummyPassword')}"
            asset_type = "Credential"
        command = ["uip", "resource", "assets", "create", name, value, "--type", asset_type, "--output", "json"]
        if folder:
            command.extend(["--folder-path", folder])
        store = first_value(record, ["CredentialStoreKey", "CredentialStoreId"])
        if store and asset_type.lower() in {"credential", "secret"}:
            command.extend(["--credential-store-key", store])
        return [command]
    if entity == "queues":
        command = ["uip", "resource", "queues", "create", name, "--output", "json"]
        if folder:
            command.extend(["--folder-path", folder])
        retries = first_value(record, ["MaxNumberOfRetries", "MaxRetries"])
        if retries:
            command.extend(["--max-retries", retries])
        return [command]
    if entity == "storage_buckets":
        command = ["uip", "resource", "buckets", "create", name, "--output", "json"]
        if folder:
            command.extend(["--folder-path", folder])
        append_if_value(command, "--description", first_value(record, ["Description"]))
        append_if_value(command, "--identifier", first_value(record, ["Identifier"]))
        append_if_value(command, "--storage-provider", first_value(record, ["StorageProvider"]))
        append_if_value(command, "--storage-parameters", first_value(record, ["StorageParameters"]))
        append_if_value(command, "--storage-container", first_value(record, ["StorageContainer"]))
        append_if_value(command, "--credential-store-key", first_value(record, ["CredentialStoreKey", "CredentialStoreId"]))
        append_if_value(command, "--external-name", first_value(record, ["ExternalName"]))
        append_if_value(command, "--options", first_value(record, ["Options"]))
        return [command]
    if entity == "packages":
        destination = str(package_file_path(config, record))
        return [["uip", "or", "packages", "upload", destination, "--output", "json"]]
    if entity == "processes":
        package_key = first_value(record, ["PackageId", "PackageKey", "ProcessKey"])
        version = first_value(record, ["PackageVersion", "Version"])
        command = ["uip", "or", "processes", "create", "--name", name, "--package-key", package_key, "--output", "json"]
        if version:
            command.extend(["--package-version", version])
        if folder:
            command.extend(["--folder-path", folder])
        return [command]
    if entity == "calendars":
        command = ["uip", "or", "calendars", "create", name, "--output", "json"]
        append_if_value(command, "--time-zone", first_value(record, ["TimeZoneId", "TimeZone"], "UTC"))
        return [command]
    if entity == "triggers":
        trigger_type = first_value(record, ["Type", "TriggerType"], "time").lower()
        release_key = first_value(record, ["ReleaseKey", "ProcessKey"])
        command = ["uip", "resource", "triggers", "create", "--type", trigger_type, "--name", name, "--release-key", release_key, "--output", "json"]
        if folder:
            command.extend(["--folder-path", folder])
        cron = first_value(record, ["Cron", "CronExpression"])
        if cron:
            command.extend(["--cron", cron])
        timezone = first_value(record, ["TimeZoneId", "TimeZone"], "UTC")
        if trigger_type == "time":
            command.extend(["--time-zone", timezone])
        append_if_value(command, "--runtime-type", first_value(record, ["RuntimeType", "RobotType"], "Unattended"))
        append_if_value(command, "--job-priority", first_value(record, ["JobPriority", "Priority"]))
        enabled = record.get("Enabled")
        if (enabled not in (None, "") and not truthy(enabled)) or truthy(record.get("Disabled")):
            command.append("--disabled")
        return [command]
    if entity == "webhooks":
        command = ["uip", "resource", "webhooks", "create", name, "--url", first_value(record, ["Url", "URL", "EndpointUrl"]), "--output", "json"]
        append_if_value(command, "--description", first_value(record, ["Description"]))
        events = record.get("Events") or record.get("EventTypes")
        if isinstance(events, list):
            event_names = [str(item.get("Name") if isinstance(item, dict) else item) for item in events if item]
            if event_names:
                command.extend(["--events", ",".join(event_names)])
        else:
            append_if_value(command, "--events", events)
        append_if_value(command, "--secret", config.get("webhook_dummy_secret"))
        if truthy(record.get("AllowInsecureSsl")) or truthy(record.get("AllowInsecureSSL")):
            command.append("--allow-insecure-ssl")
        return [command]
    fail(f"No apply command registered for {entity}")


def validate_plan(config: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if plan.get("migration_plan_version") != PLAN_VERSION:
        errors.append(f"Unexpected plan version: {plan.get('migration_plan_version')}")
    action_entities = [action.get("entity") for action in plan.get("actions", [])]
    ordered = [entity for entity in ENTITY_ORDER for action_entity in action_entities if action_entity == entity]
    if action_entities != ordered:
        errors.append("Actions are not in dependency order.")
    selected = set(selected_entities(config))
    for action in plan.get("actions", []):
        entity = action.get("entity")
        if entity not in selected:
            errors.append(f"Action includes unselected entity: {entity}")
        if not action.get("identity"):
            errors.append(f"Action for {entity} is missing identity.")
        if "source_record" not in action:
            errors.append(f"Action {action.get('identity')} is missing source_record.")
        if entity == "assets" and is_credential_asset(action.get("source_record", {})):
            if action.get("credential_asset_password_mode") != "dummy":
                errors.append(f"Credential asset {action.get('identity')} must use dummy mode in v1.")
        if entity == "packages":
            package_path = package_file_path(config, action.get("source_record", {}))
            if not package_path.exists():
                errors.append(f"Package file is not staged for {action.get('identity')}: {package_path}")
            elif package_path.stat().st_size == 0:
                errors.append(f"Package file is empty for {action.get('identity')}: {package_path}")
    return errors


def apply_plan(config: dict[str, Any], plan: dict[str, Any], yes: bool, max_actions: int | None = None) -> dict[str, Any]:
    if not yes:
        fail("Refusing to apply without --yes. Review and validate the plan first.")
    errors = validate_plan(config, plan)
    if errors:
        fail("Plan validation failed before apply:\n- " + "\n- ".join(errors))
    manual_actions = [action for action in plan.get("actions", []) if action.get("operation") == "manual_review" or action.get("entity") not in APPLY_SUPPORTED_ENTITIES]
    if manual_actions:
        sample = ", ".join(f"{action.get('entity')}:{action.get('identity')}" for action in manual_actions[:10])
        fail(
            "Plan contains manual_review actions that cannot be applied automatically. "
            "Migrate/review those entities separately or generate a plan containing only supported apply entities. "
            f"Sample: {sample}"
        )
    if direct_rest_enabled(config.get("target", {})):
        unsupported = [action for action in plan.get("actions", []) if action.get("entity") not in DIRECT_REST_APPLY_SUPPORTED_ENTITIES]
        if unsupported:
            sample = ", ".join(f"{action.get('entity')}:{action.get('identity')}" for action in unsupported[:10])
            fail(f"Plan contains actions that direct_rest apply cannot handle. Sample: {sample}")
        return apply_plan_direct_rest(config, plan, max_actions)
    maybe_switch_tenant(config, "target")
    results = {"applied_at": now_utc(), "commands": [], "manual_remediation": plan.get("manual_remediation", [])}
    actions = plan.get("actions", [])
    if max_actions is not None:
        actions = actions[:max_actions]
    for action in actions:
        for command in action_command(action, config):
            run_command(command, capture=True)
            results["commands"].append({"identity": action["identity"], "command": command})
            interval = int(config.get("request_interval_ms", 0) or 0)
            if interval > 0:
                time.sleep(interval / 1000)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and execute UiPath Orchestrator Lift-and-Shift migrations.")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init-config", help="Interactively create a local migration config without saving secrets.")
    init_parser.add_argument("--out", default="migration.local.json")
    init_parser.add_argument("--overwrite", action="store_true")

    discover_parser = sub.add_parser("discover", help="Discover source or target entities.")
    discover_parser.add_argument("--config", required=True)
    discover_parser.add_argument("--side", choices=["source", "target"], required=True)
    discover_parser.add_argument("--out")

    stage_parser = sub.add_parser("stage-packages", help="Download source package .nupkg files into the staging folder.")
    stage_parser.add_argument("--config", required=True)
    stage_parser.add_argument("--discovery", help="Optional source discovery JSON to reuse instead of discovering again.")
    stage_parser.add_argument("--out")

    plan_parser = sub.add_parser("plan", help="Generate an ordered migration plan.")
    plan_parser.add_argument("--config", required=True)
    plan_parser.add_argument("--out", required=True)
    plan_parser.add_argument("--source-discovery", help="Optional source discovery JSON to reuse instead of discovering again.")
    plan_parser.add_argument("--target-discovery", help="Optional target discovery JSON to reuse instead of discovering again.")

    apply_parser = sub.add_parser("apply", help="Apply an existing migration plan.")
    apply_parser.add_argument("--config", required=True)
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--out")
    apply_parser.add_argument("--max-actions", type=int, help="Apply only the first N actions from the plan for canary testing.")
    apply_parser.add_argument("--yes", action="store_true")

    validate_parser = sub.add_parser("validate", help="Validate an existing migration plan.")
    validate_parser.add_argument("--config", required=True)
    validate_parser.add_argument("--plan", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-config":
        init_config_interactive(args.out, args.overwrite)
        return 0

    config = normalize_config(read_json(args.config))
    if args.command == "discover":
        payload = discover(config, args.side)
        if args.out:
            write_json(args.out, payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "stage-packages":
        payload = stage_packages(config, args.discovery)
        if args.out:
            write_json(args.out, payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command == "plan":
        payload = make_plan(config, args.source_discovery, args.target_discovery)
        write_json(args.out, payload)
        print(f"Wrote migration plan: {args.out}")
        return 0
    if args.command == "validate":
        errors = validate_plan(config, read_json(args.plan))
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("Plan validation passed.")
        return 0
    if args.command == "apply":
        payload = apply_plan(config, read_json(args.plan), args.yes, args.max_actions)
        if args.out:
            write_json(args.out, payload)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    fail(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


TEST_SCRIPT = r'''#!/usr/bin/env python3
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
'''


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip(), encoding="utf-8", newline="\n")


def main() -> None:
    write(TARGET / "SKILL.md", SKILL_MD)
    write(TARGET / "references" / "orchestrator-manager-2.6.2.md", ORCHESTRATOR_MANAGER_REF)
    write(TARGET / "references" / "entity-mapping.json", json.dumps(ENTITY_MAPPING, indent=2, sort_keys=True) + "\n")
    write(TARGET / "references" / "config-example.json", json.dumps(CONFIG_EXAMPLE, indent=2, sort_keys=True) + "\n")
    write(TARGET / "scripts" / "uip_cloud_migration.py", MIGRATION_SCRIPT)
    write(TARGET / "scripts" / "test_uip_cloud_migration.py", TEST_SCRIPT)
    print(f"Wrote UiPath cloud migration skill to {TARGET}")


if __name__ == "__main__":
    main()
