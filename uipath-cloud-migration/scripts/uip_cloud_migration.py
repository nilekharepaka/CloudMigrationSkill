#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape as xml_escape


PLAN_VERSION = "uipath-cloud-migration/v2"
APPLY_BATCH_SIZE_DEFAULT = 10
QUEUE_ITEM_BATCH_SIZE_DEFAULT = 10
# Apply is intentionally fail-fast per logical item. A failed CLI/API call is
# recorded with its exact reason instead of being repeated by the engine.
APPLY_RETRY_COUNT = 0
APPLY_MAX_ATTEMPTS = 1


def iter_batches(items: list[Any], batch_size: int) -> list[list[Any]]:
    """Return sequential, bounded batches without changing dependency order."""
    size = max(1, int(batch_size or APPLY_BATCH_SIZE_DEFAULT))
    return [items[index:index + size] for index in range(0, len(items), size)]


class ApplyAbort(SystemExit):
    """Fail-fast apply stop that preserves partial results for the audit report."""

    def __init__(self, message: str, results: dict[str, Any]):
        self.message = message
        self.results = results
        super().__init__(2)


def retry_apply_operation(
    operation: Callable[[], Any],
    *,
    retry_count: int = APPLY_RETRY_COUNT,
    delay_seconds: float = 0.0,
) -> tuple[bool, Any, int, str]:
    """Run one logical apply operation exactly once.

    ``retry_count`` and ``delay_seconds`` remain accepted for compatibility
    with callers of older engine versions, but automatic retries are disabled.
    A failed operation is returned immediately with the current CLI/API or
    exception reason so the operator can fix the cause instead of waiting for
    repeated calls that are likely to fail the same way.
    """
    del retry_count, delay_seconds
    global LAST_FAILURE_DETAIL
    LAST_FAILURE_DETAIL = ""
    try:
        return True, operation(), 1, ""
    except SystemExit as error:
        exit_text = str(error).strip()
        if exit_text in {"", "1", "2"}:
            exit_text = ""
        return False, None, 1, LAST_FAILURE_DETAIL or exit_text or "apply operation failed"
    except Exception as error:
        return False, None, 1, str(error) or error.__class__.__name__


def require_analysis_report(plan: dict[str, Any]) -> None:
    """Refuse target writes unless analyze produced a real report on disk."""
    report_value = plan.get("analysis_report")
    if not report_value:
        fail(
            "Refusing to apply: this plan was not generated with an analysis report. "
            "The pre-migration analysis is mandatory and must be reviewed before any target write. "
            "Run analyze, review the report, then apply."
        )
    report_path = Path(str(report_value))
    if not report_path.exists() or report_path.stat().st_size == 0:
        fail(
            f"Refusing to apply: the analysis report is missing or empty: {report_path}. "
            "Run analyze again and review the report before applying."
        )
    html_value = plan.get("analysis_report_html")
    if html_value:
        html_path = Path(str(html_value))
        if not html_path.exists() or html_path.stat().st_size == 0:
            fail(
                f"Refusing to apply: the companion HTML analysis report is missing or empty: {html_path}. "
                "Run analyze again and review both reports before applying."
            )


# Python's default urllib User-Agent ("Python-urllib/3.x") is a known scraper
# signature that some edge WAFs (Cloudflare in front of at least one observed
# Automation Cloud staging environment) challenge with an HTML interstitial
# instead of serving the API response - authentication succeeds, the request
# just never reaches Orchestrator. An honest, self-identifying User-Agent
# (not a browser or curl impersonation) is enough to avoid that signature.
USER_AGENT = "uipath-cloud-migration-engine/1.0"

# Apply order is dependency-safe and intentionally defers the expensive work.
# Configuration entities run first. Packages and libraries are reconciled late,
# immediately before the process definitions that depend on package versions.
# Queue items are always the final content phase, after their queues exist.
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
    "calendars",
    "webhooks",
    "feeds",
    "settings",
    "packages",
    "libraries",
    "processes",
    "triggers",
    "bucket_files",
    "queue_items",
]

# Human-readable phase names are stamped into plans and stage reports so the
# operator can see why slow binary/content work is deliberately late.
ENTITY_PHASES = {
    "configuration": {
        "label": "Configuration entities",
        "entities": [
            "folders", "credential_stores", "roles", "users", "machines",
            "robots", "environments", "assets", "queues", "storage_buckets",
            "calendars", "webhooks", "feeds", "settings",
        ],
    },
    "binaries": {
        "label": "Late binary reconciliation",
        "entities": ["packages", "libraries"],
    },
    "dependent_definitions": {
        "label": "Package-dependent definitions",
        "entities": ["processes", "triggers"],
    },
    "content": {
        "label": "Final content transfer",
        "entities": ["bucket_files", "queue_items"],
    },
}
ENTITY_PHASE_BY_ENTITY = {
    entity: phase_name
    for phase_name, phase in ENTITY_PHASES.items()
    for entity in phase["entities"]
}
APPLY_SUPPORTED_ENTITIES = {
    "folders",
    "credential_stores",
    "roles",
    "users",
    "machines",
    "assets",
    "queues",
    "storage_buckets",
    "packages",
    "libraries",
    "processes",
    "calendars",
    "triggers",
    "webhooks",
    "settings",
    "bucket_files",
    "queue_items",
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
    "bucket_file": "bucket_files",
    "bucket_files": "bucket_files",
    "bucket-files": "bucket_files",
    "bucketfiles": "bucket_files",
    "storage_bucket_files": "bucket_files",
    "queue_item": "queue_items",
    "queue_items": "queue_items",
    "queue-items": "queue_items",
    "queueitems": "queue_items",
    "transactions": "queue_items",
}


# ---------------------------------------------------------------------------
# Per-entity migration profile, used to build the pre-migration analysis.
#
# scope        where the entity lives
# write_path   how apply writes it to a Cloud target
# migrates     fields and relationships that do carry over
# retained     what stays behind, and why
# depends_on   entity families that must be applied first
# prerequisite what a human must create or enable in the target before apply
# post_action  what a human must do after apply
# risk         the failure mode worth stating before approval
# ---------------------------------------------------------------------------
ENTITY_PROFILES: dict[str, dict[str, str]] = {
    "folders": {
        "scope": "Tenant",
        "write_path": "uip CLI",
        "migrates": "Fully-qualified path, hierarchy, description, feed type, permission model, provision type",
        "retained": "Folder-level user and machine assignments",
        "depends_on": "—",
        "prerequisite": "—",
        "post_action": "Assign users and machines to folders",
        "risk": "A source path that should be renamed in the target is an explicit operator decision; the engine recreates paths as-is",
    },
    "credential_stores": {
        "scope": "Tenant",
        "write_path": "Orchestrator API",
        "migrates": "Name, type, host name, additional configuration",
        "retained": "Provider secret and protected configuration — never returned by discovery",
        "depends_on": "—",
        "prerequisite": "—",
        "post_action": "Re-authenticate any store fronting an external vault before credential assets will resolve",
        "risk": "Store exists but cannot resolve secrets until re-authenticated",
    },
    "roles": {
        "scope": "Tenant",
        "write_path": "uip CLI (create, then permission grant)",
        "migrates": "Name, type, permission set",
        "retained": "User assignments; built-in and static roles are never recreated",
        "depends_on": "—",
        "prerequisite": "—",
        "post_action": "Verify permission grants and reassign users",
        "risk": "Create and grant are separate calls — a failed grant leaves a role with no access rather than a missing role",
    },
    "users": {
        "scope": "Tenant / org identity",
        "write_path": "uip CLI (directory import)",
        "migrates": "Directory principal import, folder role assignment",
        "retained": "Local On-Prem users, identity records, credentials",
        "depends_on": "roles",
        "prerequisite": "Principal must already exist in the target organization, and user import must be enabled in config",
        "post_action": "Confirm principals resolved and role assignments are correct",
        "risk": "Orchestrator metadata carries no identity — an unresolvable principal cannot be created to make the plan look clean",
    },
    "machines": {
        "scope": "Tenant",
        "write_path": "uip CLI",
        "migrates": "Machine template, slot allocation, description",
        "retained": "Machine keys, licence allocation, robot registrations",
        "depends_on": "—",
        "prerequisite": "—",
        "post_action": "Generate keys, register machines and robots, allocate licences",
        "risk": "Templates alone do not make a runnable target — registration is a separate project",
    },
    "robots": {
        "scope": "Tenant (legacy)",
        "write_path": "None — no create endpoint exists",
        "migrates": "Nothing",
        "retained": "Everything",
        "depends_on": "machines, users",
        "prerequisite": "Decide the modern equivalent (folders plus machine templates) before migration",
        "post_action": "Re-register robots against the target",
        "risk": "Legacy construct with no Cloud equivalent; a like-for-like copy is the wrong goal",
    },
    "environments": {
        "scope": "Tenant (legacy)",
        "write_path": "None — absent from the current API",
        "migrates": "Nothing",
        "retained": "Everything",
        "depends_on": "robots",
        "prerequisite": "Map each environment onto target folders before migration",
        "post_action": "Recreate the grouping as folders",
        "risk": "Superseded by folders; no endpoint to migrate to",
    },
    "assets": {
        "scope": "Folder",
        "write_path": "uip CLI",
        "migrates": "Name, type, value, scope, description, tags",
        "retained": "Real credential passwords; per-robot values (they bind to robots, which cannot be created)",
        "depends_on": "folders, credential_stores",
        "prerequisite": "A target credential store must be named in config before credential or secret assets can be applied",
        "post_action": "Set real passwords on every credential asset; re-establish per-robot values after robots are registered",
        "risk": "A credential asset with a real name and a dummy password is a live misconfiguration, not a placeholder",
    },
    "queues": {
        "scope": "Folder",
        "write_path": "uip CLI",
        "migrates": "Definition, retries, SLA and risk SLA, unique reference, encryption, both retention policies",
        "retained": "Items and all transaction history",
        "depends_on": "folders",
        "prerequisite": "—",
        "post_action": "—",
        "risk": "Enabling unique reference before migrating items is what makes an item re-run safe",
    },
    "storage_buckets": {
        "scope": "Folder",
        "write_path": "uip CLI",
        "migrates": "Built-in buckets: definition and options",
        "retained": "External-provider buckets entirely — they carry provider credentials",
        "depends_on": "folders",
        "prerequisite": "Recreate external-provider buckets and their provider configuration by hand",
        "post_action": "Verify external buckets separately",
        "risk": "An external bucket silently absent from the target breaks any automation reading from it",
    },
    "packages": {
        "scope": "Tenant feed",
        "write_path": "uip CLI (upload)",
        "migrates": "Package binaries, version by version",
        "retained": "Feed configuration; versions already present in the target are skipped",
        "depends_on": "—",
        "prerequisite": "Binaries must be staged locally before apply",
        "post_action": "—",
        "risk": "An unstaged package fails validation, and every process bound to it fails after",
    },
    "libraries": {
        "scope": "Tenant / host feed",
        "write_path": "uip CLI (upload)",
        "migrates": "Tenant-feed library binaries",
        "retained": "Host-feed libraries — shared source infrastructure, not tenant content",
        "depends_on": "—",
        "prerequisite": "Binaries must be staged locally before apply",
        "post_action": "Decide whether host-feed libraries become tenant libraries in the target",
        "risk": "A missing library breaks every package that depends on it",
    },
    "processes": {
        "scope": "Folder",
        "write_path": "uip CLI",
        "migrates": "Package binding and version, entry point, input arguments, priority, tags, retention, attended visibility",
        "retained": "Job and execution history",
        "depends_on": "folders, packages",
        "prerequisite": "—",
        "post_action": "Test-run before cutover",
        "risk": "Cannot be applied before its package version resolves in the target feed",
    },
    "calendars": {
        "scope": "Tenant",
        "write_path": "Orchestrator API",
        "migrates": "Name, timezone, excluded dates",
        "retained": "—",
        "depends_on": "—",
        "prerequisite": "—",
        "post_action": "—",
        "risk": "A calendar missing its excluded dates silently removes holiday protection from every trigger using it",
    },
    "triggers": {
        "scope": "Folder",
        "write_path": "uip CLI",
        "migrates": "Type (time, queue, API), schedule, bindings, thresholds, priority, runtime type, enablement",
        "retained": "Execution history",
        "depends_on": "processes, queues, calendars",
        "prerequisite": "—",
        "post_action": "Review every migrated trigger before it fires",
        "risk": "Bindings resolve by target key, so dependencies must exist first; an enabled trigger starts production work on arrival",
    },
    "webhooks": {
        "scope": "Tenant",
        "write_path": "uip CLI",
        "migrates": "URL, event subscriptions, description",
        "retained": "Signing secret",
        "depends_on": "the entities whose events they subscribe to",
        "prerequisite": "—",
        "post_action": "Rotate the signing secret in the target",
        "risk": "Until rotation the receiving endpoint cannot verify payload authenticity",
    },
    "feeds": {
        "scope": "Tenant",
        "write_path": "None — no create endpoint exists",
        "migrates": "Nothing",
        "retained": "Everything",
        "depends_on": "—",
        "prerequisite": "Configure target feeds by hand before packages are expected to resolve",
        "post_action": "Verify feed configuration",
        "risk": "Feed configuration is deployment-specific and carries credentials",
    },
    "settings": {
        "scope": "Tenant",
        "write_path": "uip CLI",
        "migrates": "Non-secret key/value settings",
        "retained": "Keys naming a password, API key, secret, token or connection string; deployment and NuGet URLs",
        "depends_on": "—",
        "prerequisite": "—",
        "post_action": "Set secret-bearing settings deliberately in the target",
        "risk": "Copying a deployment URL would repoint the target at the source's feeds",
    },
    "bucket_files": {
        "scope": "Folder",
        "write_path": "Orchestrator API (pre-signed URIs)",
        "migrates": "File content of built-in buckets, streamed source to target",
        "retained": "Contents of external-provider buckets",
        "depends_on": "storage_buckets",
        "prerequisite": "—",
        "post_action": "Spot-check file counts and sizes",
        "risk": "Total bytes drive apply duration more than any configuration entity",
    },
    "queue_items": {
        "scope": "Folder",
        "write_path": "Orchestrator API (bulk add)",
        "migrates": "Items in New state: content, reference, priority, defer and due dates",
        "retained": "Every other state, and all transaction history",
        "depends_on": "queues",
        "prerequisite": "—",
        "post_action": "Reconcile item counts against the source New-state count",
        "risk": "A re-run duplicates items in a queue that does not enforce unique references",
    },
}


def entity_profile(entity: str) -> dict[str, str]:
    return ENTITY_PROFILES.get(entity, {
        "scope": "", "write_path": "", "migrates": "", "retained": "",
        "depends_on": "", "prerequisite": "", "post_action": "", "risk": "",
    })


LAST_FAILURE_DETAIL: str = ""


def fail(message: str, code: int = 2) -> None:
    global LAST_FAILURE_DETAIL
    LAST_FAILURE_DETAIL = message
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def abort_apply(message: str, results: dict[str, Any]) -> None:
    """Stop fail-fast apply while retaining the partial audit payload."""
    global LAST_FAILURE_DETAIL
    LAST_FAILURE_DETAIL = message
    results["apply_status"] = "failed"
    results["failure_reason"] = message
    print(f"ERROR: {message}", file=sys.stderr)
    raise ApplyAbort(message, results)


def preflight_apply_failure(plan: dict[str, Any] | None, message: str) -> dict[str, Any]:
    """Build a reportable result when apply stops before its loops start."""
    return {
        "applied_at": now_utc(),
        "apply_status": "failed_before_apply",
        "retry_count": APPLY_RETRY_COUNT,
        "max_attempts": APPLY_MAX_ATTEMPTS,
        "commands": [],
        "failures": [{
            "index": 0,
            "batch_number": 0,
            "attempts": 0,
            "max_attempts": APPLY_MAX_ATTEMPTS,
            "entity": "preflight",
            "identity": "",
            "stage": "preflight",
            "command": "",
            "reason": message,
        }],
        "manual_remediation": (plan or {}).get("manual_remediation", []),
    }


def persist_apply_artifacts(
    config: dict[str, Any],
    plan: dict[str, Any],
    payload: dict[str, Any],
    results_path: str | Path,
    report_path: str | Path,
) -> None:
    """Write both apply artifacts, including when apply stopped with an error."""
    write_json(results_path, payload)
    write_apply_report(config, plan, payload, report_path)


def require_analysis_for_staging(config: dict[str, Any]) -> dict[str, Any]:
    """Require an analyzed, blocker-free plan before binary downloads.

    The operator approval itself remains an explicit human decision. This gate
    ensures the command cannot stage from a bare ``plan`` artifact or while the
    analyzed readiness result contains a hard blocker.
    """
    plan_path = default_state_artifact(config, "migration-plan.json")
    if not plan_path.exists():
        fail(
            f"Refusing to stage binaries: analyzed migration plan not found at {plan_path}. "
            "Run analyze, review and approve its report, then stage."
        )
    plan = read_json(plan_path)
    try:
        require_analysis_report(plan)
    except SystemExit:
        raise
    blockers = readiness_blocker_errors(config, plan)
    if blockers:
        fail(
            "Refusing to stage binaries while readiness blockers remain:\n- "
            + "\n- ".join(blockers)
        )
    return plan


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def xlsx_column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xlsx_cell_ref(row: int, column: int) -> str:
    return f"{xlsx_column_name(column)}{row}"


def xlsx_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def xlsx_cell(row: int, column: int, value: Any, style: int = 0) -> str:
    ref = xlsx_cell_ref(row, column)
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style_attr}><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = xml_escape(xlsx_text(value), {'"': "&quot;"})
    preserve = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t{preserve}>{text}</t></is></c>'


def xlsx_sheet_xml(rows: list[list[Any]], freeze_header: bool = True) -> str:
    max_cols = max((len(row) for row in rows), default=1)
    max_rows = max(len(rows), 1)
    dimension = f"A1:{xlsx_cell_ref(max_rows, max_cols)}"
    cols = "".join(
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate([24, 18, 18, 22, 22, 18, 70, 70, 24, 24, 24, 24], start=1)
        if idx <= max_cols
    )
    sheet_views = '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
    if freeze_header and rows:
        sheet_views = (
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '</sheetView></sheetViews>'
        )
    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        style = 1 if row_index == 1 else 0
        cells = "".join(xlsx_cell(row_index, col_index, value, style) for col_index, value in enumerate(row, start=1))
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')
    auto_filter = f'<autoFilter ref="{dimension}"/>' if len(rows) > 1 else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="{dimension}"/>'
        f'{sheet_views}'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{cols}</cols>' if cols else ""
    ) + f'<sheetData>{"".join(sheet_rows)}</sheetData>{auto_filter}</worksheet>'


def safe_sheet_name(name: str, used: set[str]) -> str:
    cleaned = "".join("_" if char in "[]:*?/\\'" else char for char in name).strip() or "Sheet"
    cleaned = cleaned[:31]
    candidate = cleaned
    counter = 2
    while candidate in used:
        suffix = f" {counter}"
        candidate = cleaned[: 31 - len(suffix)] + suffix
        counter += 1
    used.add(candidate)
    return candidate


def state_dir_for_config(config: dict[str, Any]) -> Path:
    """Return the operator-local machine-state directory for a run.

    JSON snapshots, plans, probe results, and stage checkpoints are engine
    state, not the operator-facing deliverable. Keep them together in one
    hidden directory beside the config unless the config explicitly chooses a
    different artifact directory.
    """
    configured = str(config.get("artifact_dir") or ".migration-state")
    directory = Path(configured)
    if not directory.is_absolute():
        config_path = config.get("_config_path")
        base = Path(str(config_path)).parent if config_path else Path.cwd()
        directory = base / directory
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def default_state_artifact(config: dict[str, Any], filename: str) -> Path:
    return state_dir_for_config(config) / filename


def default_visible_artifact(config: dict[str, Any], filename: str) -> Path:
    """Return the primary human-facing artifact path beside the config."""
    config_path = config.get("_config_path")
    base = Path(str(config_path)).parent if config_path else Path.cwd()
    return base / filename


def output_path_or_state(config: dict[str, Any], explicit: str | None, filename: str) -> Path:
    """Resolve an explicit compatibility path or the hidden state default."""
    return Path(explicit) if explicit else default_state_artifact(config, filename)


def default_binary_discovery_path(config: dict[str, Any]) -> str | None:
    """Reuse the standard target snapshot when it has already been captured.

    Staging may be run independently from the full analysis workflow. Reusing
    the standard snapshot keeps that path fast and deterministic; when it is
    absent, callers fall back to a narrowly scoped live target discovery.
    """
    candidate = default_state_artifact(config, "target.json")
    return str(candidate) if candidate.exists() else None


def report_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a recursively sanitized copy for operator-facing reports.

    Discovery payloads can contain nested provider configuration or credential
    metadata. Sanitizing only the top level would make the workbook unsafe to
    share, so nested dictionaries and lists use the same key/name rules.
    """
    sensitive_tokens = (
        "password", "secret", "token", "apikey", "api_key",
        "connectionstring", "connection_string",
    )
    credential_record = "credential" in " ".join(
        str(record.get(key, "")) for key in ("Type", "ValueType", "AssetType", "CredentialStoreType")
    ).lower()
    sensitive_name = " ".join(
        str(record.get(key, "")) for key in ("Name", "Key", "SettingName", "DisplayName")
    ).lower().replace("-", "_")
    named_secret = any(token in sensitive_name for token in sensitive_tokens)
    secret_value_keys = {
        "value", "username", "user", "user_name", "credential", "credentialvalue",
        "additionalconfiguration", "configuration", "providerconfiguration",
    }

    def safe_value(key: Any, value: Any, inherited_sensitive: bool = False) -> Any:
        key_text = str(key).lower().replace("-", "_")
        redact = any(token in key_text for token in sensitive_tokens)
        if (credential_record or named_secret or inherited_sensitive) and key_text in secret_value_keys:
            redact = True
        if redact:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {child_key: safe_value(child_key, child_value, inherited_sensitive or redact)
                    for child_key, child_value in value.items()}
        if isinstance(value, list):
            return [safe_value(key, child, inherited_sensitive or redact) for child in value]
        return value

    inherited = credential_record or named_secret
    return {key: safe_value(key, value, inherited) for key, value in record.items()}


def report_record_text(record: dict[str, Any]) -> str:
    try:
        return json.dumps(report_safe_record(record), ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(report_safe_record(record))


def write_xlsx(path: str | Path, sheets: list[tuple[str, list[list[Any]]]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    normalized = [(safe_sheet_name(name, used_names), rows or [["No data"]]) for name, rows in sheets]
    workbook_sheets = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, (name, _) in enumerate(normalized, start=1)
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        for idx, _ in enumerate(normalized, start=1)
    )
    workbook_rels += (
        f'<Relationship Id="rId{len(normalized) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for idx, _ in enumerate(normalized, start=1)
    )
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
  <fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f'{overrides}</Types>'
        ))
        archive.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'
        ))
        archive.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{workbook_sheets}</sheets></workbook>'
        ))
        archive.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{workbook_rels}</Relationships>'
        ))
        archive.writestr("xl/styles.xml", styles)
        for idx, (_, rows) in enumerate(normalized, start=1):
            archive.writestr(f"xl/worksheets/sheet{idx}.xml", xlsx_sheet_xml(rows))


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
    # A legacy/local config that names folders without an explicit mode is a
    # folder-scoped migration, not a full lift-and-shift. Infer the narrower
    # mode instead of silently generating a plan that readiness must reject.
    if "migration_mode" not in normalized:
        normalized["migration_mode"] = (
            "folder_subset"
            if (normalized.get("source", {}) or {}).get("folder_paths")
            else "lift_and_shift"
        )
    normalized.setdefault("entities", ENTITY_ORDER)
    normalized.setdefault("artifact_dir", ".migration-state")
    normalized.setdefault("package_staging_folder", "DownloadedPackages")
    normalized.setdefault("request_interval_ms", 0)
    normalized.setdefault("batch_size", 1000)
    normalized.setdefault("apply_batch_size", APPLY_BATCH_SIZE_DEFAULT)
    normalized.setdefault("queue_item_batch_size", QUEUE_ITEM_BATCH_SIZE_DEFAULT)
    normalized.setdefault("continue_on_entity_error", True)
    normalized.setdefault("continue_on_apply_error", True)
    normalized.setdefault("continue_on_library_error", normalized.get("continue_on_package_error", True))
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


MIGRATION_MODES = ("lift_and_shift", "folder_subset", "entity_subset", "assessment_only")
REQUIRED_SOURCE_SCOPE = (
    "OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration "
    "OR.Jobs OR.Users OR.Robots OR.Machines OR.Webhooks OR.License"
)


def source_auth_mode(deployment_type: str) -> str:
    """Derive source authentication from the deployment; never ask for it separately."""
    return "uip" if deployment_type == "cloud_tenant" else "direct_rest"


def default_migration_config() -> dict[str, Any]:
    return {
        "migration_mode": "lift_and_shift",
        "source": {
            "tenant": "",
            "deployment_type": "msi_standalone",
            "auth_mode": "direct_rest",
            "identity_url": "",
            "orchestrator_url": "",
            "client_id_env": "UIP_ONPREM_CLIENT_ID",
            "client_secret_env": "UIP_ONPREM_CLIENT_SECRET",
            "scope": REQUIRED_SOURCE_SCOPE,
            "folder_paths": [],
            "uip_extra_args": [],
        },
        "target": {
            "authority": "https://cloud.uipath.com",
            "organization": "",
            "tenant": "",
            "uip_profile": "",
            "folder_paths": [],
            "uip_extra_args": [],
        },
        "entities": ENTITY_ORDER,
        "artifact_dir": ".migration-state",
        "package_staging_folder": "DownloadedPackages",
        "request_interval_ms": 250,
        "batch_size": 1000,
        "apply_batch_size": APPLY_BATCH_SIZE_DEFAULT,
        "queue_item_batch_size": QUEUE_ITEM_BATCH_SIZE_DEFAULT,
        "continue_on_entity_error": True,
        "continue_on_folder_error": True,
        "continue_on_package_error": True,
        "continue_on_library_error": True,
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

    # Block 1, asked first: without a reachable source and working credentials
    # nothing else matters, so connection details come before scope decisions.
    print("")
    print("--- Block 1: source connection ---")
    source = config["source"]
    source["deployment_type"] = prompt_choice(
        "Source deployment type", ["msi_standalone", "automation_suite", "cloud_tenant"], "msi_standalone"
    )
    source_auth = source_auth_mode(source["deployment_type"])
    source["auth_mode"] = source_auth
    print(f"Source auth mode is determined automatically: {source_auth}")
    source["orchestrator_url"] = prompt_text("Source Orchestrator base URL", "https://<onprem-host>", required=True).rstrip("/")
    source["tenant"] = prompt_text("Source tenant name", required=True)
    if source_auth == "direct_rest":
        default_identity = source["orchestrator_url"] + "/identity"
        source["identity_url"] = prompt_text("Source Identity URL", default_identity, required=True).rstrip("/")
        print("")
        print("The External Application must have every permission below for the selected migration engine:")
        print(f"  {source['scope']}")
        app_ready = prompt_yes_no(
            "Does the External Application exist with all of the required permissions", True
        )
        if not app_ready:
            print("")
            print("STOP: create or update the External Application with all of the permissions listed above, then re-run this setup.")
            fail("External Application is missing or does not have the required permissions.")
        source["client_id_env"] = prompt_text("Env var name holding the client ID", "UIP_ONPREM_CLIENT_ID", required=True)
        source["client_secret_env"] = prompt_text("Env var name holding the client secret", "UIP_ONPREM_CLIENT_SECRET", required=True)
        print("Values are never written here, and this tool never asks for them.")
        print("Set them yourself, directly in your own terminal - never paste a command containing")
        print("the real value into a chat session for an assistant to run on your behalf.")
        print("")
        print("If a human is running these commands directly in one continuous terminal, a")
        print("session-scoped variable is enough:")
        print(f"  export {source['client_id_env']}=...   (PowerShell: $env:{source['client_id_env']} = '...')")
        print(f"  export {source['client_secret_env']}=...")
        print("")
        print("If an assistant/agent is running these commands for you, session-scoped variables")
        print("will NOT work - each command it runs is a fresh process and will not see them.")
        print("Set a PERSISTENT variable instead, so new processes inherit it:")
        print(f"  setx {source['client_id_env']} \"...\"      (Windows; ~/.bash_profile export on macOS/Linux)")
        print(f"  setx {source['client_secret_env']} \"...\"")
    else:
        # A uip-session source needs no External Application credentials; the
        # Orchestrator URL, tenant, and deployment type are still recorded.
        source.pop("identity_url", None)
        source.pop("client_id_env", None)
        source.pop("client_secret_env", None)
        source.pop("scope", None)

    print("")
    print("--- Block 2: migration scope ---")
    print("Migration shape:")
    print("  lift_and_shift  every discoverable folder and every supported entity family")
    print("  folder_subset   only the folders you name")
    print("  entity_subset   only the entity families you name")
    print("  assessment_only read-only analysis; nothing is downloaded or applied")
    mode = prompt_choice("Migration mode", list(MIGRATION_MODES), "lift_and_shift")
    config["migration_mode"] = mode

    folder_text = prompt_text(
        "Folder paths, comma-separated; blank means ALL discoverable folders"
        + (" (mode implies all)" if mode in ("lift_and_shift", "assessment_only") else ""),
        required=(mode == "folder_subset"),
    )
    source["folder_paths"] = split_csv(folder_text)

    entity_text = prompt_text(
        "Entity families, comma-separated; blank means ALL supported families"
        + (" (mode implies all)" if mode in ("lift_and_shift", "assessment_only") else ""),
        required=(mode == "entity_subset"),
    )
    if entity_text.strip():
        config["entities"] = [canonical_entity(item) for item in split_csv(entity_text)]


    print("")
    print("--- Block 3: target and policy ---")
    target = config["target"]
    has_target = prompt_yes_no("Does a target tenant exist to compare against", True)
    if has_target:
        target["authority"] = prompt_text("Target Cloud authority/base URL", "https://cloud.uipath.com", required=True).rstrip("/")
        target["organization"] = prompt_text("Target Cloud organization name", required=True)
        target["tenant"] = prompt_text("Target Automation Cloud tenant name", required=True)
        target_folder_text = prompt_text("Target folder paths, comma-separated; blank reuses source folder scope")
        target["folder_paths"] = split_csv(target_folder_text) if target_folder_text.strip() else source["folder_paths"]
    else:
        # Source-only assessment: no diff, and every write command refuses to run.
        target["mode"] = "none"
        target["tenant"] = ""
        target["folder_paths"] = []
        print("No target configured. This produces a source inventory; staging, validation and apply are blocked.")

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
        print("Before running discovery, set these two environment variables yourself, directly in")
        print("your own terminal - never paste the command with the real value into a chat session:")
        print(f"  setx {source['client_id_env']} \"<source external app id>\"")
        print(f"  setx {source['client_secret_env']} \"<source external app secret>\"")
        print("(setx persists the variable so it is visible to new processes, including ones an")
        print("assistant runs on your behalf. A session-scoped $env:/export only works if you are")
        print("typing the discovery commands yourself, in that same terminal.)")
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


def queue_item_is_new(record: dict[str, Any]) -> bool:
    """Return whether a queue item is explicitly in the migratable New state.

    Live discovery asks Orchestrator for Status eq 'New'. Fixtures and saved
    snapshots do not get that server-side guarantee, so they fail closed: an
    absent or unknown state is not evidence that an item is New.
    """
    state = first_value(record, ["Status", "State", "QueueItemStatus"])
    return state.strip().casefold() == "new"


def filter_new_queue_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only explicitly New queue items from any discovery input."""
    return [record for record in records if queue_item_is_new(record)]


def normalize_entity_records(entity: str, records: Any) -> list[dict[str, Any]]:
    """Normalize one entity family from live, fixture, or snapshot input."""
    normalized = [record for record in (records or []) if isinstance(record, dict)]
    return filter_new_queue_items(normalized) if entity == "queue_items" else normalized


def normalize_discovery_payload(payload: Any, side: str = "unknown") -> dict[str, Any]:
    """Normalize discovery inputs before planning, including saved snapshots."""
    if not isinstance(payload, dict):
        return {
            "entities": {},
            "errors": [{
                "entity": "*", "side": side,
                "message": "Discovery payload is not an object.",
            }],
        }
    normalized = dict(payload)
    entities = dict(payload.get("entities") or {})
    notes = list(payload.get("discovery_notes") or [])
    if "queue_items" in entities:
        original = [record for record in (entities.get("queue_items") or []) if isinstance(record, dict)]
        filtered = filter_new_queue_items(original)
        excluded_count = len(original) - len(filtered)
        if excluded_count:
            notes.append({
                "entity": "queue_items",
                "side": side,
                "reason": "excluded_non_new_queue_items",
                "excluded_count": excluded_count,
                "message": f"Excluded {excluded_count} queue item(s) that were not explicitly in New state.",
            })
        entities["queue_items"] = filtered
    normalized["entities"] = entities
    if notes:
        normalized["discovery_notes"] = notes
    return normalized


def package_id(record: dict[str, Any]) -> str:
    value = first_value(record, ["PackageId", "Id", "Name"])
    if value:
        # Some endpoints expose the composite PackageId:Version value in Id
        # or PackageId, with or without a separate Version field. Normalize it
        # before matching so source and target records use the same key.
        return value.rsplit(":", 1)[0] if ":" in value else value
    key = first_value(record, ["Key"])
    return key.rsplit(":", 1)[0] if ":" in key else key


def package_version(record: dict[str, Any]) -> str:
    value = first_value(record, ["Version", "PackageVersion", "ReleaseVersion"])
    if value:
        return value
    composite = first_value(record, ["PackageId", "Id", "Name", "Key"])
    return composite.rsplit(":", 1)[1] if ":" in composite else ""


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
    """Only built-in Orchestrator buckets are eligible for automatic apply.

    External-provider configuration and provider secrets are deliberately
    manual-only. The config argument remains for call-site compatibility, but
    no opt-in flag may bypass this boundary.
    """
    provider = first_value(record, ["StorageProvider"])
    return not bool(provider)


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
    if entity == "queue_items":
        queue = first_value(record, ["QueueDefinitionName", "QueueName"])
        reference = first_value(record, ["Reference"]) or f"id:{first_value(record, ['Id'])}"
        folder = record_folder(record)
        return f"{folder}/{queue}/{reference}" if folder else f"{queue}/{reference}"
    if entity == "bucket_files":
        bucket = first_value(record, ["BucketName"])
        path = first_value(record, ["Name"])
        folder = record_folder(record)
        return f"{folder}/{bucket}/{path}" if folder else f"{bucket}/{path}"
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


_TARGET_UIP_CONTEXT: dict[str, str] = {}
_TARGET_UIP_SESSION_KEY: tuple[str, str, str, str] | None = None
_TARGET_AUTH_ENV_NAMES = (
    "UIPATH_CLI_ENABLE_ENV_AUTH",
    "UIPATH_CLI_AUTH_TOKEN",
    "UIPATH_CLI_ORGANIZATION_NAME",
    "UIPATH_CLI_ORGANIZATION_ID",
    "UIPATH_CLI_TENANT_NAME",
    "UIPATH_CLI_TENANT_ID",
)


def target_profile_name(target: dict[str, Any]) -> str:
    """Return a safe named profile derived from the configured destination."""
    explicit = str(target.get("uip_profile", "")).strip()
    if explicit:
        if not all(character.isalnum() or character in "._-" for character in explicit):
            fail("target.uip_profile may contain only letters, numbers, '.', '_' and '-'.")
        return explicit
    organization = str(target.get("organization", "target")).strip()
    tenant = str(target.get("tenant", "target")).strip()
    raw = f"migration-{organization}-{tenant}".lower()
    safe = "".join(character if character.isalnum() or character in "._-" else "-" for character in raw)
    return safe.strip("-._")[:80] or "migration-target"


def target_uip_context(config: dict[str, Any]) -> dict[str, str]:
    """Build the non-secret CLI context required by the configured Cloud target."""
    target = config.get("target", {}) or {}
    tenant = str(target.get("tenant", "")).strip()
    organization = str(target.get("organization", target.get("organization_name", ""))).strip()
    authority = str(
        target.get("authority")
        or target.get("base_url")
        or "https://cloud.uipath.com"
    ).strip().rstrip("/")
    if not tenant:
        fail("Cloud target login requires target.tenant.")
    if not organization:
        fail(
            "Cloud target login requires target.organization. "
            "The destination organization must come from intake; the current uip session is not used."
        )
    if not authority:
        fail("Cloud target login requires target.authority or target.base_url.")
    return {
        "profile": target_profile_name(target),
        "authority": authority,
        "organization": organization,
        "tenant": tenant,
    }


def target_uip_command(args: list[str]) -> list[str]:
    """Bind a target uip command to the verified destination profile."""
    if not args or str(args[0]).lower() != "uip" or not _TARGET_UIP_CONTEXT:
        return args
    if "--profile" in args:
        return args
    return [args[0], "--profile", _TARGET_UIP_CONTEXT["profile"], *args[1:]]


def target_session_matches(data: dict[str, Any], context: dict[str, str]) -> bool:
    """Compare a login/status payload with the configured destination."""
    if not isinstance(data, dict):
        return False
    organization = str(data.get("OrganizationName") or data.get("Organization") or "").strip()
    tenant = str(data.get("TenantName") or data.get("Tenant") or "").strip()
    base_url = str(data.get("BaseUrl") or data.get("Url") or "").strip().rstrip("/")
    if organization.casefold() != context["organization"].casefold():
        return False
    if tenant.casefold() != context["tenant"].casefold():
        return False
    if not base_url:
        return False
    try:
        expected_host = urllib.parse.urlparse(context["authority"]).netloc.casefold()
        actual_host = urllib.parse.urlparse(base_url).netloc.casefold()
    except ValueError:
        return False
    return bool(expected_host and actual_host and expected_host == actual_host)


def ensure_target_uip_session(config: dict[str, Any]) -> dict[str, str] | None:
    """Force and verify destination login once per engine process.

    The login runs in the child uip process with inherited environment-auth
    variables removed. This prevents Studio's current tenant from overriding
    the organization and tenant supplied during migration intake.
    """
    global _TARGET_UIP_CONTEXT, _TARGET_UIP_SESSION_KEY
    if config.get("fixture_dir") or direct_rest_enabled(config.get("target", {}) or {}):
        return None
    context = target_uip_context(config)
    key = (
        context["profile"],
        context["authority"],
        context["organization"],
        context["tenant"],
    )
    if _TARGET_UIP_SESSION_KEY == key:
        return context
    _TARGET_UIP_CONTEXT = context
    run_command([
        "uip", "login",
        "--profile", context["profile"],
        "--authority", context["authority"],
        "--organization", context["organization"],
        "--tenant", context["tenant"],
        "--output", "json",
    ])
    status_payload = run_command([
        "uip", "login", "status",
        "--profile", context["profile"],
        "--output", "json",
    ])
    status_data = status_payload.get("Data") if isinstance(status_payload, dict) else None
    if not target_session_matches(status_data or {}, context):
        _TARGET_UIP_SESSION_KEY = None
        fail(
            "Destination-specific uip login did not resolve to the configured target. "
            f"Expected organization {context['organization']} and tenant {context['tenant']} "
            f"at {context['authority']}. Refusing to use the current uip session."
        )
    _TARGET_UIP_SESSION_KEY = key
    return context


def run_command(args: list[str], *, capture: bool = True, env: dict[str, str] | None = None) -> Any:
    logical_args = target_uip_command(args)
    command = resolve_command(logical_args)
    child_env = None
    if _TARGET_UIP_CONTEXT and logical_args and str(logical_args[0]).lower() == "uip":
        child_env = os.environ.copy()
        for name in _TARGET_AUTH_ENV_NAMES:
            child_env.pop(name, None)
    if env is not None:
        if child_env is None:
            child_env = os.environ.copy()
        child_env.update(env)
    completed = subprocess.run(command, text=True, capture_output=capture, check=False, env=child_env)
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


def check_source_reachability(orchestrator_url: str, identity_url: str = "") -> dict[str, Any]:
    """Test whether the source is reachable from this machine - no credentials involved.

    Reachability is a fact about the network, evaluated once the URL is known,
    never a question put to the operator: they usually cannot tell whether this
    specific machine can reach a private-network Orchestrator, but a direct
    request settles it in seconds. Any HTTP response at all - even an error
    status - means the host was reached; only a connection-level failure (DNS,
    refused, timeout) means it was not.
    """
    orchestrator_url = orchestrator_url.rstrip("/")
    identity_url = (identity_url or f"{orchestrator_url}/identity").rstrip("/")
    check_url = f"{identity_url}/.well-known/openid-configuration"
    result: dict[str, Any] = {
        "checked_at": now_utc(),
        "checked_url": check_url,
        "reachable": False,
        "notes": [],
    }
    request = urllib.request.Request(check_url, headers={"User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read(256)
        result["reachable"] = True
        result["notes"].append(f"Reached {check_url} and it responded.")
    except urllib.error.HTTPError as error:
        # The host answered - a 404 or 500 still proves the network path works;
        # it only means this exact path is wrong, which discovery does not use.
        result["reachable"] = True
        result["notes"].append(f"Reached {check_url} (it returned HTTP {error.code}).")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        result["notes"].append(
            f"Could not reach {check_url} from this machine ({error}). "
            "If the Orchestrator is on a private network, run the migration commands from a host "
            "that can reach it rather than from here."
        )
    return result


def probe_succeeded(payload: dict[str, Any]) -> bool:
    """Return whether a source probe is safe to use for the intake handoff."""
    return (
        payload.get("reachable") is True
        and payload.get("credentials_present") is True
        and payload.get("authenticated") is True
        and payload.get("scope_verified") is True
        and not payload.get("missing_scopes")
    )


def probe_source(config: dict[str, Any]) -> dict[str, Any]:
    """Authenticate to the source and report what it can see, before any scope is set.

    Runs before a tenant is chosen: the client-credentials token is host-level, so
    the tenant list can be enumerated and offered back to the operator instead of
    being typed from memory. Enumeration needs OR.Administration; a tenant-scoped
    External Application may see only its own tenant, or be refused outright, and
    that is reported rather than treated as failure.
    """
    side = dict(config.get("source") or {})
    if not direct_rest_enabled(side):
        fail("probe applies to a direct_rest source. Set the source auth mode accordingly.")
    side.pop("tenant", None)          # host-level call: no tenant header
    side.setdefault("batch_size", config.get("batch_size", 1000))

    result: dict[str, Any] = {
        "probed_at": now_utc(),
        "orchestrator_url": side.get("orchestrator_url", ""),
        "identity_url": side.get("identity_url", ""),
        "scopes_requested": side.get("scope", ""),
        "scopes_granted": [],
        "missing_scopes": [],
        "scope_verified": False,
        "token_expires_in": None,
        "reachable": False,
        "credentials_present": None,
        "authenticated": False,
        "tenant_enumeration": "not attempted",
        "tenants": [],
        "notes": [],
    }

    # Reachability first, unconditionally - it needs no credentials, so nothing
    # about the operator's setup should gate whether it gets checked.
    reachability = check_source_reachability(result["orchestrator_url"], result["identity_url"])
    result["reachable"] = reachability["reachable"]
    result["notes"].extend(reachability["notes"])
    if not result["reachable"]:
        return result

    # Credentials absent from the environment is a local setup problem, not a
    # network or auth one, and is now known to be distinct from reachability.
    id_env = side.get("client_id_env", "UIP_ONPREM_CLIENT_ID")
    secret_env = side.get("client_secret_env", "UIP_ONPREM_CLIENT_SECRET")
    absent = [name for name in (id_env, secret_env) if not os.environ.get(str(name))]
    if absent:
        result["credentials_present"] = False
        setx_lines = "\n".join(f'  setx {name} "<real value>"' for name in absent)
        result["notes"].append(
            "Not attempted: " + " and ".join(str(a) for a in absent) + " not set in the process "
            "environment.\n"
            "Run these yourself, directly in your own terminal (never paste a command containing "
            "the real value into a chat session):\n"
            f"{setx_lines}\n"
            "setx writes a persistent variable, but only processes started after you run it can see "
            "it — if an assistant is running commands for you, tell it once this is done rather than "
            "pasting the values, and expect to restart that session so it picks up the new variable. "
            "Then re-run this command."
        )
        return result
    result["credentials_present"] = True

    try:
        get_direct_rest_token(side)
        result["authenticated"] = True
        granted = side.get("_granted_scope", "")
        result["scopes_granted"] = str(granted).split() if granted else []
        requested = str(result["scopes_requested"] or "").split()
        result["missing_scopes"] = [scope for scope in requested if scope not in result["scopes_granted"]]
        result["scope_verified"] = bool(granted) and not result["missing_scopes"]
        result["token_expires_in"] = side.get("_token_expires_in")
        if not granted:
            result["notes"].append(
                "The token response did not include a scope field; the granted permission set could not be verified."
            )
        elif result["missing_scopes"]:
            result["notes"].append(
                "The External Application is missing required permissions: "
                + " ".join(result["missing_scopes"])
                + ". Update the application and re-run the probe."
            )
    except (urllib.error.URLError, OSError) as error:
        # Reachability already passed above, so a failure here is the token
        # endpoint specifically, not the host in general - worth saying so.
        result["notes"].append(f"The Identity endpoint stopped responding during the token request ({error}).")
        return result
    except SystemExit:
        result["notes"].append(
            "Reached the Identity endpoint, and the credentials are set, but the token request was "
            "rejected. Confirm the client ID and secret belong to an External Application on THIS "
            "Orchestrator, and that every scope requested is one the application actually grants."
        )
        return result

    try:
        payload = direct_rest_get(side, "/odata/Tenants?$orderby=Name")
        records = extract_items(payload)
        for record in records:
            name = first_value(record, ["Name", "DisplayName", "TenancyName"])
            if not name:
                continue
            result["tenants"].append({
                "name": name,
                "display_name": first_value(record, ["DisplayName", "Name"]),
                "id": first_value(record, ["Id", "Key"]),
                "enabled": record.get("IsActive", record.get("Enabled", True)),
            })
        if result["tenants"]:
            result["tenant_enumeration"] = "ok"
        else:
            result["tenant_enumeration"] = "empty"
            result["notes"].append(
                "Authentication succeeded but no tenants were returned. The External Application is "
                "probably tenant-scoped rather than host-scoped. Enter the tenant name manually."
            )
    except (urllib.error.URLError, OSError) as error:
        result["tenant_enumeration"] = "unreachable"
        result["notes"].append(f"Authenticated, but the Orchestrator URL could not be reached ({error}).")
    except SystemExit:
        result["tenant_enumeration"] = "refused"
        result["notes"].append(
            "Authentication succeeded but tenant enumeration was refused. This needs OR.Administration "
            "(or .Read) and a host-scoped External Application. Enter the tenant name manually - "
            "discovery itself does not need this endpoint."
        )
    return result


def target_absent(config: dict[str, Any]) -> bool:
    """True when no target tenant exists yet.

    A pre-purchase or pre-provisioning assessment has a source and nothing to
    compare it against. The inventory and disposition analysis are still fully
    meaningful; only the diff and the skip-as-existing analysis are not.
    """
    target = config.get("target") or {}
    if str(target.get("mode", "")).strip().lower() in {"none", "absent", "unprovisioned"}:
        return True
    return not str(target.get("tenant", "")).strip()


def require_target(config: dict[str, Any], operation: str) -> None:
    if target_absent(config):
        fail(
            f"Cannot {operation}: no target tenant is configured. "
            "This config is a source-only assessment. Set the target tenant before "
            "staging, validating, or applying."
        )


def direct_rest_enabled(side_config: dict[str, Any]) -> bool:
    return side_config.get("auth_mode") == "direct_rest"


def get_direct_rest_token(side_config: dict[str, Any]) -> str:
    cached = side_config.get("_access_token")
    if cached:
        return str(cached)
    # Every folder-scoped entity call re-enters this function, so a source
    # that is genuinely down would otherwise re-run the full retry dance once
    # per entity per folder - dozens of times, each taking minutes. Once the
    # source has failed 3 attempts, remember that and fail instantly for the
    # rest of this run instead of repeating a doomed network round trip.
    cached_failure = side_config.get("_token_failure")
    if cached_failure:
        fail(f"Source already confirmed unreachable this run ({cached_failure})")
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
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    # A transient network blip here must not crash the whole run - every other
    # on-prem call already retries (see open_with_retry); this is the one call
    # that ran ahead of all of them and had been left out. Capped at 3 attempts
    # with a short per-attempt timeout, specifically so a genuinely unreachable
    # source is confirmed in well under a minute, not tens of minutes.
    payload: dict[str, Any] = {}
    attempts = 3
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            fail(f"Token request failed ({exc.code}): {details}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt >= attempts:
                message = f"Token request failed after {attempts} attempts: {exc}"
                side_config["_token_failure"] = message
                fail(message)
            time.sleep(delay)
            delay *= 2
    token = payload.get("access_token")
    if not token:
        fail(f"Token response did not contain access_token: {payload}")
    side_config["_access_token"] = token
    # Keep only non-secret token metadata for the intake probe. Never expose or
    # persist the bearer token itself in a report, snapshot, or command output.
    granted_scope = payload.get("scope")
    if isinstance(granted_scope, str):
        side_config["_granted_scope"] = granted_scope
    if payload.get("expires_in") is not None:
        side_config["_token_expires_in"] = payload.get("expires_in")
    return str(token)


def direct_rest_headers(side_config: dict[str, Any], folder_id: Any | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {get_direct_rest_token(side_config)}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
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


def open_with_retry(request: urllib.request.Request, timeout: int, what: str, attempts: int = 3) -> bytes:
    """Read a request, retrying transient network failures.

    A single slow or dropped call must not abort a multi-hour discovery, and a
    persistent one has to surface as fail() rather than a traceback so that the
    continue-on-error settings actually govern it.
    """
    delay = 2.0
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = str(error)
            if attempt < attempts:
                time.sleep(delay)
                delay *= 2
                continue
    fail(f"{what} failed after {attempts} attempts: {last}")
    return b""


def direct_rest_get(side_config: dict[str, Any], endpoint: str, folder_id: Any | None = None) -> dict[str, Any]:
    base = str(side_config.get("orchestrator_url", "")).rstrip("/")
    if not base:
        fail("direct_rest auth requires orchestrator_url.")
    request = urllib.request.Request(base + endpoint, headers=direct_rest_headers(side_config, folder_id), method="GET")
    try:
        return json.loads(open_with_retry(request, 60, f"GET {endpoint}").decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        fail(f"GET {endpoint} failed ({exc.code}): {details}")
    return {}


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
        try:
            payload = direct_rest_get(side_config, append_query(endpoint, {"$top": top, "$skip": skip}), folder_id)
        except SystemExit:
            if skip > 0:
                raise  # already got some pages back; a failure mid-stream is a real error
            # A handful of endpoints (Feeds on some versions) reject $top/$skip
            # paging outright. Fall back to a single unpaged fetch rather than
            # losing the whole entity family to a query-shape mismatch.
            path, _, _ = endpoint.partition("?")
            for fallback in (endpoint, path):
                try:
                    print(
                        f"NOTE: this Orchestrator rejected paging options on {path}; "
                        "retrying as a single unpaged request.",
                        file=sys.stderr,
                    )
                    return extract_items(direct_rest_get(side_config, fallback, folder_id))
                except SystemExit:
                    continue
            raise
        batch = extract_items(payload)
        records.extend(batch)
        if len(batch) < top:
            return records
        skip += top


# ---------------------------------------------------------------------------
# Cloud target REST
#
# The uip CLI covers most target writes, but some fields it does not expose are
# needed for a faithful migration: calendar excluded dates, credential store
# creation, queue item content, bucket file content. Those go through the
# Orchestrator OData API using the CLI's own session, obtained from
# `uip login refresh` — the documented machine-consumption contract that emits
# a guaranteed-valid access token. No second External App, and the token is
# held in memory only, never written to a file.
# ---------------------------------------------------------------------------

_CLOUD_SESSION: dict[str, Any] = {}


def invalidate_cloud_session() -> None:
    _CLOUD_SESSION.clear()


def cloud_session(config: dict[str, Any], min_validity_minutes: int = 10) -> dict[str, str]:
    # Cloud REST is still target work: establish and verify the intake-selected
    # destination before consulting or refreshing the in-memory token cache.
    target_context = ensure_target_uip_session(config)
    target_key = tuple(target_context.values()) if target_context else None
    cached = _CLOUD_SESSION.get("session")
    if (
        cached
        and _CLOUD_SESSION.get("target_key") == target_key
        and time.time() < float(_CLOUD_SESSION.get("safe_until", 0))
    ):
        return cached
    extra = [str(arg) for arg in (config.get("target", {}) or {}).get("uip_extra_args", [])]
    payload = run_command([
        "uip", "login", "refresh",
        "--login-validity", str(min_validity_minutes),
        "--output", "json",
        *extra,
    ])
    data = payload.get("Data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("AccessToken"):
        fail(
            "Could not obtain a Cloud access token from `uip login refresh`. "
            "Log in to the target tenant and retry."
        )
    base_url = str(data.get("BaseUrl", "")).rstrip("/")
    organization = first_value(data, ["OrganizationName", "Organization"])
    tenant = first_value(data, ["TenantName", "Tenant"])
    if not (base_url and organization and tenant):
        fail("`uip login refresh` did not report base URL, organization, and tenant.")
    if target_context and not target_session_matches(data, target_context):
        fail(
            "`uip login refresh` returned a session for a different destination. "
            f"Expected organization {target_context['organization']} and tenant "
            f"{target_context['tenant']} at {target_context['authority']}."
        )
    session = {
        "token": str(data["AccessToken"]),
        "base": f"{base_url}/{organization}/{tenant}/orchestrator_",
        "tenant": tenant,
    }
    _CLOUD_SESSION["session"] = session
    _CLOUD_SESSION["target_key"] = target_key
    # Refresh guarantees the token for min_validity_minutes; re-ask before then.
    _CLOUD_SESSION["safe_until"] = time.time() + max(60, (min_validity_minutes - 2) * 60)
    return session


def cloud_rest_headers(config: dict[str, Any], folder_id: Any | None = None) -> dict[str, str]:
    session = cloud_session(config)
    headers = {
        "Authorization": f"Bearer {session['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if folder_id not in (None, ""):
        headers["X-UIPATH-OrganizationUnitId"] = str(folder_id)
    return headers


def cloud_rest_request(
    config: dict[str, Any],
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    folder_id: Any | None = None,
    _attempt: int = 1,
) -> dict[str, Any]:
    session = cloud_session(config)
    url = session["base"] + endpoint
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=body, headers=cloud_rest_headers(config, folder_id), method=method.upper()
    )
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        # A 403 that comes back as an HTML "Continue with UiPath Platform"
        # interstitial - instead of a JSON OData error body - is an edge/WAF
        # challenge page, not a real permission denial (which always returns
        # JSON). The known trigger (a missing/default User-Agent) is fixed at
        # the request level via USER_AGENT; this retry is a defensive fallback
        # for any other transient edge hiccup that produces the same signature.
        is_edge_interstitial = error.code == 403 and detail.lstrip().startswith("<!DOCTYPE")
        if is_edge_interstitial and _attempt < 4:
            time.sleep(2 * _attempt)
            invalidate_cloud_session()
            return cloud_rest_request(config, method, endpoint, payload, folder_id, _attempt + 1)
        fail(f"Cloud REST {method.upper()} {endpoint} failed ({error.code}): {detail[:600]}")
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def presigned_download(uri: str, headers: dict[str, str] | None = None) -> bytes:
    """Fetch bucket content from a pre-signed URI. Never send the bearer token here."""
    combined = {"User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(uri, headers=combined, method="GET")
    with urllib.request.urlopen(request) as response:
        return response.read()


def presigned_upload(uri: str, data: bytes, headers: dict[str, str] | None = None) -> None:
    combined = {"Content-Type": "application/octet-stream", "User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(uri, data=data, headers=combined, method="PUT")
    try:
        with urllib.request.urlopen(request) as response:
            response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:400]
        fail(f"Bucket file upload failed ({error.code}): {detail}")


def presigned_uri_and_headers(payload: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Extract the URI and any required headers from a Get*Uri response."""
    data = payload.get("Data") if isinstance(payload.get("Data"), dict) else payload
    uri = first_value(data, ["Uri", "uri", "Url", "url"])
    headers: dict[str, str] = {}
    raw_headers = data.get("Headers") or data.get("RequiresAuth")
    if isinstance(raw_headers, dict):
        keys = raw_headers.get("Keys") or raw_headers.get("keys")
        values = raw_headers.get("Values") or raw_headers.get("values")
        if isinstance(keys, list) and isinstance(values, list):
            headers = {str(k): str(v) for k, v in zip(keys, values)}
    return uri, headers


def direct_rest_folder_label(record: dict[str, Any]) -> str:
    return first_value(record, ["FullyQualifiedName", "FullyQualifiedPath", "FullPath", "DisplayName", "Name"])


def direct_rest_folder_id(record: dict[str, Any]) -> Any:
    return record.get("Id") or record.get("ID") or record.get("Key")


_FOLDER_SCOPE_CACHE: dict[str, list[dict[str, Any]]] = {}


def direct_rest_folders_for_scope(side_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Folders in scope, fetched once per run.

    Every folder-scoped entity family needs this list, so without caching the
    same call is repeated a dozen times over a single discovery - wasted calls
    against a production Orchestrator, and a dozen extra chances for a transient
    timeout to take a family down.
    """
    cache_key = "|".join([
        str(side_config.get("orchestrator_url", "")),
        str(side_config.get("tenant", "")),
        ",".join(sorted(str(item) for item in side_config.get("folder_paths", []) if str(item))),
    ])
    cached = _FOLDER_SCOPE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    folders = direct_rest_get_all(side_config, "/odata/Folders?$orderby=FullyQualifiedName")
    requested = set(str(item) for item in side_config.get("folder_paths", []) if str(item))
    if requested:
        folders = [
            folder for folder in folders
            if direct_rest_folder_label(folder) in requested
            or first_value(folder, ["Name"]) in requested
        ]
    _FOLDER_SCOPE_CACHE[cache_key] = folders
    return folders


_EXPAND_SUPPORT: dict[str, bool] = {}


def strip_expand(endpoint: str) -> str:
    """Remove the $expand clause from an OData endpoint, keeping the rest intact."""
    if "?" not in endpoint:
        return endpoint
    path, _, query = endpoint.partition("?")
    kept = [
        part for part in query.split("&")
        if part and not part.split("=", 1)[0] in ("$expand", "%24expand")
    ]
    return path + ("?" + "&".join(kept) if kept else "")


def usable_endpoint(side_config: dict[str, Any], endpoint: str, folder_id: Any | None = None) -> str:
    """Return the endpoint this Orchestrator will actually accept.

    Navigation-property expansions carry fields the migration needs, but older
    Orchestrator versions reject some of them outright with a 400 and would take
    the whole entity family down with them. Probe once per endpoint, and fall back
    to the unexpanded form rather than losing the family.
    """
    if "$expand=" not in endpoint:
        return endpoint
    cached = _EXPAND_SUPPORT.get(endpoint)
    if cached is True:
        return endpoint
    if cached is False:
        return strip_expand(endpoint)
    # Probe quietly: a rejected expansion is an expected outcome on older
    # versions, not an error worth printing as one.
    base = str(side_config.get("orchestrator_url", "")).rstrip("/")
    probe = urllib.request.Request(
        base + append_query(endpoint, {"$top": 1}),
        headers=direct_rest_headers(side_config, folder_id), method="GET",
    )
    try:
        with urllib.request.urlopen(probe, timeout=30) as response:
            response.read()
        _EXPAND_SUPPORT[endpoint] = True
        return endpoint
    except Exception:
        _EXPAND_SUPPORT[endpoint] = False
        reduced = strip_expand(endpoint)
        print(
            f"NOTE: this Orchestrator rejected the expansion on {endpoint.split('?')[0]}; "
            "continuing without it. Fields that only arrive expanded will be absent, "
            "and the analysis reports them as needing manual attention.",
            file=sys.stderr,
        )
        return reduced


def direct_rest_discover_entity(config: dict[str, Any], side: str, entity: str) -> list[dict[str, Any]]:
    side_config = config.get(side, {})
    side_config.setdefault("batch_size", config.get("batch_size", 1000))
    if entity == "folders":
        return direct_rest_get_all(side_config, "/odata/Folders?$orderby=FullyQualifiedName")
    tenant_endpoint_by_entity = {
        "credential_stores": "/odata/CredentialStores?$orderby=Name",
        # Permissions and role assignments are navigation properties: without the
        # expansion the records come back without the fields apply needs.
        "roles": "/odata/Roles?$expand=Permissions&$orderby=Name",
        "users": "/odata/Users?$expand=RolesList&$orderby=UserName",
        "machines": "/odata/Machines?$orderby=Name",
        "calendars": "/odata/Calendars?$orderby=Name",
        "webhooks": "/odata/Webhooks?$orderby=Name",
        "feeds": "/odata/Feeds?$orderby=Name",
        "settings": "/odata/Settings",
    }
    if entity in tenant_endpoint_by_entity:
        return direct_rest_get_all(side_config, usable_endpoint(side_config, tenant_endpoint_by_entity[entity]))
    if entity == "packages":
        records = direct_rest_get_all(side_config, "/odata/Processes?$orderby=Key")
        for record in records:
            record.setdefault("FolderPath", record.get("FolderName") or record.get("TenantName") or "")
        return records
    if entity == "libraries":
        records = direct_rest_get_all(side_config, "/odata/Libraries?$orderby=Id")
        for record in records:
            record.setdefault("FolderPath", record.get("FolderName") or record.get("TenantName") or "")
            record.setdefault("LibraryFeedScope", "Tenant")
        return records
    if entity == "queue_items":
        # Only New items are migratable. Copying InProgress items would double-run
        # them and copying completed items would re-process finished work.
        records: list[dict[str, Any]] = []
        for folder in direct_rest_folders_for_scope(side_config):
            folder_id = direct_rest_folder_id(folder)
            folder_name = direct_rest_folder_label(folder)
            queues = direct_rest_get_all(side_config, "/odata/QueueDefinitions?$orderby=Name", folder_id)
            queue_names = {str(q.get("Id")): first_value(q, ["Name"]) for q in queues}
            items = direct_rest_get_all(
                side_config,
                "/odata/QueueItems?$filter=Status%20eq%20'New'&$orderby=Id",
                folder_id,
            )
            for item in items:
                item.setdefault("FolderPath", folder_name)
                item.setdefault(
                    "QueueDefinitionName",
                    queue_names.get(str(item.get("QueueDefinitionId")), ""),
                )
                records.append(item)
        return records
    if entity == "bucket_files":
        records = []
        for folder in direct_rest_folders_for_scope(side_config):
            folder_id = direct_rest_folder_id(folder)
            folder_name = direct_rest_folder_label(folder)
            for bucket in direct_rest_get_all(side_config, "/odata/Buckets?$orderby=Name", folder_id):
                if first_value(bucket, ["StorageProvider"]):
                    continue  # external providers are not migrated
                bucket_key = first_value(bucket, ["Identifier", "Key", "Id"])
                bucket_id = first_value(bucket, ["Id", "Key"])
                listing = direct_rest_get(
                    side_config,
                    f"/odata/Buckets({bucket_id})/UiPath.Server.Configuration.OData.GetFiles?directory=%2F&recursive=true",
                    folder_id,
                )
                for item in extract_items(listing):
                    full_path = first_value(item, ["FullPath", "Path", "Name"])
                    if not full_path or truthy(item.get("IsDirectory")):
                        continue
                    records.append({
                        "Name": full_path,
                        "BucketName": first_value(bucket, ["Name"]),
                        "BucketId": bucket_id,
                        "BucketKey": bucket_key,
                        "Size": item.get("Size"),
                        "ContentType": first_value(item, ["ContentType"]),
                        "FolderPath": folder_name,
                    })
        return records
    endpoint_by_entity = {
        "assets": "/odata/Assets?$orderby=Name",
        "queues": "/odata/QueueDefinitions?$orderby=Name",
        "robots": "/odata/Robots?$orderby=Name",
        "environments": "/odata/Environments?$orderby=Name",
        "storage_buckets": "/odata/Buckets?$orderby=Name",
        "processes": "/odata/Releases?$expand=ReleaseVersions&$orderby=Name",
        # Release, queue, and calendar names are what apply resolves into target
        # keys, so they must be expanded rather than left as source IDs.
        "triggers": "/odata/ProcessSchedules?$expand=Release,QueueDefinition,Calendar&$orderby=Name",
    }
    endpoint = endpoint_by_entity.get(entity)
    if not endpoint:
        fail(f"No direct_rest endpoint registered for {entity}")
    records: list[dict[str, Any]] = []
    for folder in direct_rest_folders_for_scope(side_config):
        folder_id = direct_rest_folder_id(folder)
        folder_name = direct_rest_folder_label(folder)
        for item in direct_rest_get_all(side_config, usable_endpoint(side_config, endpoint, folder_id), folder_id):
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
        "apply_batch_size": max(1, int(config.get("apply_batch_size", APPLY_BATCH_SIZE_DEFAULT) or APPLY_BATCH_SIZE_DEFAULT)),
        "retry_count": APPLY_RETRY_COUNT,
        "max_attempts": APPLY_MAX_ATTEMPTS,
        "actions": [],
        "errors": [],
        "manual_remediation": plan.get("manual_remediation", []),
    }
    actions = plan.get("actions", [])
    if max_actions is not None:
        actions = actions[:max_actions]
    interval = int(config.get("request_interval_ms", 0) or 0)
    batch_size = results["apply_batch_size"]
    continue_on_error = truthy(config.get("continue_on_apply_error", True))
    for batch_number, batch in enumerate(iter_batches(actions, batch_size), start=1):
        for offset, action in enumerate(batch):
            index = (batch_number - 1) * batch_size + offset + 1
            ok, result, attempts, error_text = retry_apply_operation(
                lambda action=action: direct_rest_apply_one(config, action, state),
                delay_seconds=interval / 1000 if interval > 0 else 0.0,
            )
            if ok:
                result["index"] = index
                result["batch_number"] = batch_number
                result["attempts"] = attempts
                result["max_attempts"] = APPLY_MAX_ATTEMPTS
                results["actions"].append(result)
            else:
                failure = {
                    "index": index,
                    "batch_number": batch_number,
                    "attempts": attempts,
                    "max_attempts": APPLY_MAX_ATTEMPTS,
                    "identity": action.get("identity"),
                    "entity": action.get("entity"),
                    "stage": "rest",
                    "error": error_text or LAST_FAILURE_DETAIL or "apply operation failed",
                }
                results["errors"].append(failure)
                if not continue_on_error:
                    abort_apply(str(failure["error"]), results)
            # A failed logical item is isolated by design. With
            # continue_on_apply_error enabled, record only that item as failed
            # and continue with the next item and entity family.
    return results


def maybe_switch_tenant(config: dict[str, Any], side: str) -> None:
    side_config = config.get(side, {})
    if direct_rest_enabled(side_config):
        return
    if side == "target":
        # The target is always selected from intake, never from Studio's
        # ambient uip session. ensure_target_uip_session() performs a fresh
        # destination-specific login and verifies the resulting context.
        ensure_target_uip_session(config)
        return
    tenant = side_config.get("tenant")
    if tenant and not config.get("fixture_dir"):
        extra = [str(arg) for arg in side_config.get("uip_extra_args", [])]
        run_command(["uip", "login", "tenant", "set", str(tenant), "--output", "json", *extra])


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
        return ["uip", "or", "libraries", "list", "--limit", str(side_config.get("batch_size", 1000)), "--output", "json", *extra]
    if entity == "webhooks":
        return ["uip", "or", "webhooks", "list", "--output", "json", *extra]
    if entity == "storage_buckets":
        return ["uip", "or", "buckets", "list", "--all-folders", "--output", "json", *extra]
    if entity == "assets":
        command = ["uip", "or", "assets", "list", "--output", "json", *extra]
    elif entity == "queues":
        command = ["uip", "or", "queues", "list", "--output", "json", *extra]
    elif entity == "packages":
        return ["uip", "or", "packages", "list", "--output", "json", *extra]
    elif entity == "processes":
        command = ["uip", "or", "processes", "list", "--output", "json", *extra]
    elif entity == "triggers":
        command = ["uip", "or", "triggers", "list", "--folder-path", folder or "", "--output", "json", *extra]
        return [part for part in command if part != ""]
    elif entity in {"robots", "environments"}:
        return []
    else:
        fail(f"No list command registered for {entity}")
    if folder:
        command.extend(["--folder-path", folder])
    return command


CLOUD_REST_CONTENT_ENTITIES = {"queue_items", "bucket_files"}

# `uip or feeds list` has no --limit/--offset flags at all (confirmed via
# --help) - it always returns every accessible feed in one response. Every
# other non-folder-scoped entity does support pagination, so this is a
# narrow, verified exception rather than a general carve-out.
NON_PAGINATED_ENTITIES = {"feeds"}


def cloud_folder_index(config: dict[str, Any]) -> dict[str, Any]:
    """Target folder path -> folder id, for the folder-scope header."""
    payload = cloud_rest_request(config, "GET", "/odata/Folders?$top=1000")
    index: dict[str, Any] = {}
    for record in extract_items(payload):
        label = folder_path(record)
        if label:
            index[label] = record.get("Id")
    return index


def cloud_discover_content(config: dict[str, Any], entity: str, side: str = "target") -> list[dict[str, Any]]:
    """Inventory queue items and bucket files through the Cloud API.

    Used for either side: as a source inventory for a Cloud-to-Cloud migration,
    and on the target so a re-run can skip content that already landed. Folder
    scope is read from whichever side is being inventoried.
    """
    folders = cloud_folder_index(config)
    scope = [str(item) for item in (config.get(side, {}) or {}).get("folder_paths", []) if str(item)]
    records: list[dict[str, Any]] = []
    for label, folder_id in folders.items():
        if scope and label not in scope:
            continue
        if entity == "queue_items":
            # This endpoint enforces $top <= 100 - unlike Folders/Buckets, which
            # accept $top=1000 - so a hardcoded single request silently truncates
            # any queue with more than 100 New-state items. Page with $skip
            # until a short page proves there is nothing left.
            page_size = 100
            skip = 0
            while True:
                payload = cloud_rest_request(
                    config, "GET",
                    f"/odata/QueueItems?$filter=Status%20eq%20'New'&$top={page_size}&$skip={skip}&$expand=QueueDefinition",
                    folder_id=folder_id,
                )
                page = extract_items(payload)
                for item in page:
                    item.setdefault("FolderPath", label)
                    item.setdefault("QueueDefinitionName", nested_name(item, ["QueueDefinition"]))
                    records.append(item)
                if len(page) < page_size:
                    break
                skip += page_size
        else:
            buckets = cloud_rest_request(config, "GET", "/odata/Buckets?$top=1000", folder_id=folder_id)
            for bucket in extract_items(buckets):
                if first_value(bucket, ["StorageProvider"]):
                    continue
                bucket_id = first_value(bucket, ["Id", "Key"])
                listing = cloud_rest_request(
                    config, "GET",
                    f"/odata/Buckets({bucket_id})/UiPath.Server.Configuration.OData.GetFiles"
                    "?directory=%2F&recursive=true",
                    folder_id=folder_id,
                )
                for item in extract_items(listing):
                    full_path = first_value(item, ["FullPath", "Path", "Name"])
                    if not full_path or truthy(item.get("IsDirectory")):
                        continue
                    records.append({
                        "Name": full_path,
                        "BucketName": first_value(bucket, ["Name"]),
                        "BucketId": bucket_id,
                        "FolderPath": label,
                    })
    return records


def discover_entity(config: dict[str, Any], side: str, entity: str, known_folders: list[str]) -> list[dict[str, Any]]:
    fixture = fixture_path(config, side, entity)
    if fixture:
        return normalize_entity_records(entity, extract_items(read_json(fixture)))

    side_config = config.get(side, {})
    if direct_rest_enabled(side_config):
        return direct_rest_discover_entity(config, side, entity)
    if entity in CLOUD_REST_CONTENT_ENTITIES:
        # No uip CLI surface exists for queue item or bucket file content, on
        # either side. Read them through the Cloud API using the CLI's session.
        return cloud_discover_content(config, entity, side)

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
    if entity in NON_PAGINATED_ENTITIES:
        return extract_items(run_command(command))
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


def inventory_keys(discovery: dict[str, Any], entities: list[str]) -> dict[str, list[str]]:
    """Return the target's natural-key inventory, preserving duplicates."""
    discovered = discovery.get("entities") or {}
    return {
        entity: sorted(
            identity(entity, record) or "<missing-identity>"
            for record in normalize_entity_records(entity, discovered.get(entity, []))
        )
        for entity in entities
    }


def inventory_signature(discovery: dict[str, Any], entities: list[str]) -> str:
    """Fingerprint target natural keys, including duplicate records.

    Server IDs, timestamps, and other volatile fields are intentionally not
    included. The signature answers the safety question that matters before an
    apply: is the target's natural-key inventory still the one the operator
    approved, including duplicate/ambiguous records?
    """
    return inventory_keys_signature(inventory_keys(discovery, entities), entities)


def inventory_keys_signature(keys_by_entity: dict[str, list[str]], entities: list[str]) -> str:
    """Fingerprint a previously extracted natural-key map."""
    entries = [
        [entity, str(key)]
        for entity in entities
        for key in sorted(str(item) for item in (keys_by_entity.get(entity) or []))
    ]
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def expected_target_inventory_keys(plan: dict[str, Any], entities: list[str]) -> dict[str, list[str]]:
    """Return the target keys allowed immediately before a resume apply.

    The approved target inventory is the baseline. Objects named by the plan
    are also allowed because a canary or a prior bounded batch may already have
    created them; escalation is explicitly a resume, not a fresh migration.
    Unexpected additions, removals, or duplicate natural keys remain drift.
    """
    baseline = plan.get("target_inventory_keys") or {}
    actions_by_entity: dict[str, list[str]] = {entity: [] for entity in entities}
    for action in plan.get("actions", []):
        entity = str(action.get("entity", ""))
        if entity in actions_by_entity:
            actions_by_entity[entity].append(str(action.get("identity", "")))
    return {
        entity: sorted(list(baseline.get(entity, [])) + actions_by_entity[entity])
        for entity in entities
    }


def package_source_key(record: dict[str, Any]) -> str:
    return first_value(record, ["Key"]) or identity("packages", record)


def library_source_key(record: dict[str, Any]) -> str:
    return first_value(record, ["Key"]) or identity("libraries", record)


def binary_match_text(value: Any) -> str:
    """Normalize a package/library natural-key component for matching."""
    return str(value or "").strip().casefold()


def package_match_key(record: dict[str, Any]) -> tuple[str, str]:
    """Natural key for a package version, independent of server IDs."""
    return binary_match_text(package_id(record)), binary_match_text(package_version(record))


def library_feed_scope(record: dict[str, Any]) -> str:
    """Return the source/target feed scope reported for a library."""
    if library_is_host_feed(record):
        return "host"
    raw = first_value(
        record,
        ["LibraryFeedScope", "FeedScope", "FeedType", "SourceFeedScope"],
    ).strip().casefold()
    return "host" if raw in {"host", "hostfeed", "host feed"} else "tenant"


def library_match_key(record: dict[str, Any]) -> tuple[str, str, str]:
    """Natural key for an available library version.

    Feed IDs are intentionally not part of the key: source and target feed IDs
    are different across tenants. The package ID and exact version are the
    identity; scope is retained for reporting and future feed-aware matching.
    """
    name = package_id(record) or record_name(record)
    return binary_match_text(name), binary_match_text(package_version(record)), library_feed_scope(record)


def binary_target_index(entity: str, records: list[dict[str, Any]]) -> dict[tuple[str, ...], dict[str, Any]]:
    """Index unique target package/library versions without relying on IDs.

    Duplicate natural keys are deliberately omitted. A duplicate or otherwise
    ambiguous target inventory must not be treated as proof that a source
    binary is safely satisfied. The host-feed tenant-equivalent alias is built
    only after the primary keys have passed this uniqueness check, preventing
    aliases from masking duplicate records.
    """
    primary: dict[tuple[str, ...], dict[str, Any]] = {}
    counts: dict[tuple[str, ...], int] = {}
    for record in records:
        key = package_match_key(record) if entity == "packages" else library_match_key(record)
        if not all(key):
            continue
        counts[key] = counts.get(key, 0) + 1
        primary.setdefault(key, record)

    result = {
        key: record
        for key, record in primary.items()
        if counts.get(key) == 1
    }
    if entity != "libraries":
        return result

    # A target host-feed library can satisfy the same exact package/version as
    # a tenant-feed source library. Expose that fallback only when the host
    # primary key itself is unique and no tenant primary key is ambiguous.
    for key, record in result.copy().items():
        if key[2] != "host":
            continue
        tenant_key = (key[0], key[1], "tenant")
        if counts.get(tenant_key, 0) == 0 and tenant_key not in result:
            result[tenant_key] = record
    return result


def load_target_binary_records(
    config: dict[str, Any],
    entity: str,
    discovery_path: str | None,
) -> tuple[list[dict[str, Any]], str]:
    """Load target package/library inventory for download avoidance.

    A target inventory is mandatory for the optimization. If the operator did
    not provide a snapshot, capture only this binary family from the live target
    rather than redownloading or blindly assuming the target is empty.
    """
    if discovery_path:
        payload = read_json(discovery_path)
        errors = [
            item for item in payload.get("errors", [])
            if str(item.get("entity", "")) == entity and str(item.get("side", "target")) == "target"
        ]
        if errors:
            fail(
                f"Cannot optimize {entity} staging because the target discovery for {entity} failed. "
                "Fix the target discovery or rerun staging without a stale snapshot."
            )
        entities = payload.get("entities") or {}
        if entity not in entities:
            fail(f"Target discovery snapshot does not contain '{entity}'. Re-capture target discovery before staging.")
        return list(entities.get(entity) or []), f"snapshot:{discovery_path}"
    if target_absent(config):
        fail(f"Cannot stage {entity} without a configured target; exact target-version matching is required first.")
    maybe_switch_tenant(config, "target")
    records = discover_entity(config, "target", entity, [])
    return records, "live target discovery"


def package_file_path(config: dict[str, Any], record: dict[str, Any]) -> Path:
    staging = Path(config.get("package_staging_folder", "DownloadedPackages"))
    name = safe_file_part(package_id(record) or record_name(record) or "package")
    version = safe_file_part(package_version(record) or "unknown")
    return staging / f"{name}.{version}.nupkg"


def library_file_path(config: dict[str, Any], record: dict[str, Any]) -> Path:
    staging = Path(config.get("package_staging_folder", "DownloadedPackages"))
    name = safe_file_part(package_id(record) or record_name(record) or "library")
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


def library_download_endpoint(config: dict[str, Any], record: dict[str, Any]) -> str:
    key = library_source_key(record)
    if not key:
        fail(f"Library record is missing a package key/id: {record}")
    escaped_key = key.replace("'", "''")
    endpoint = f"/odata/Libraries/UiPath.Server.Configuration.OData.DownloadPackage(key='{escaped_key}')"
    source_config = config.get("source", {})
    feed_id = first_value(record, ["FeedId", "FeedID", "feedId"]) or source_config.get("library_feed_id") or config.get("library_feed_id")
    if feed_id not in (None, ""):
        endpoint += "?" + urllib.parse.urlencode({"feedId": str(feed_id)})
    return endpoint


def library_is_host_feed(record: dict[str, Any]) -> bool:
    if truthy(record.get("IsHostFeed")) or truthy(record.get("HostFeed")) or truthy(record.get("IsHostPackage")):
        return True
    tenant_marker = record.get("IsTenantPackage")
    if tenant_marker not in (None, "") and not truthy(tenant_marker):
        return True
    feed_scope = first_value(record, ["FeedScope", "LibraryFeedScope", "Scope", "SourceFeedScope"]).strip().lower()
    if feed_scope in {"host", "hostfeed", "host feed"}:
        return True
    feed_type = first_value(record, ["FeedType", "FeedName", "Feed"]).strip().lower()
    return feed_type in {"host", "hostfeed", "host feed"}


def load_package_records(config: dict[str, Any], discovery_path: str | None) -> list[dict[str, Any]]:
    if discovery_path:
        discovery = read_json(discovery_path)
    else:
        discovery = discover(config, "source")
    return list(discovery.get("entities", {}).get("packages", []))


def load_library_records(config: dict[str, Any], discovery_path: str | None) -> list[dict[str, Any]]:
    if discovery_path:
        discovery = read_json(discovery_path)
    else:
        discovery = discover(config, "source")
    return [
        record
        for record in discovery.get("entities", {}).get("libraries", [])
        if not library_is_host_feed(record)
    ]


def stage_packages(
    config: dict[str, Any],
    discovery_path: str | None = None,
    target_discovery_path: str | None = None,
) -> dict[str, Any]:
    packages = load_package_records(config, discovery_path)
    source_config = config.get("source", {})
    report: dict[str, Any] = {
        "staged_at": now_utc(),
        "phase": ENTITY_PHASE_BY_ENTITY["packages"],
        "package_staging_folder": config.get("package_staging_folder", "DownloadedPackages"),
        "target_match_source": None,
        "source_count": len(packages),
        "target_count": 0,
        "source_package_count": len(packages),
        "target_package_count": 0,
        "already_in_target_count": 0,
        "already_staged_count": 0,
        "downloaded_count": 0,
        "failed_count": 0,
        "packages": [],
        "skipped_packages": [],
        "failed_packages": [],
    }
    target_discovery_path = target_discovery_path or default_binary_discovery_path(config)
    target_packages, target_match_source = load_target_binary_records(
        config, "packages", target_discovery_path,
    )
    target_index = binary_target_index("packages", target_packages)
    report["target_match_source"] = target_match_source
    report["target_count"] = len(target_packages)
    report["target_package_count"] = len(target_packages)
    if not packages:
        return report

    def target_skip(record: dict[str, Any]) -> bool:
        match_key = package_match_key(record)
        target_record = target_index.get(match_key)
        if target_record is None:
            return False
        entry = {
            "identity": identity("packages", record),
            "source_key": package_source_key(record),
            "match_key": list(match_key),
            "target_match": True,
            "target_match_source": target_match_source,
            "reason": "exact_package_id_and_version_already_in_target",
            "target_record": report_safe_record(target_record),
            "status": "already_in_target",
        }
        report["skipped_packages"].append(entry)
        report["already_in_target_count"] += 1
        return True

    if direct_rest_enabled(source_config):
        for record in packages:
            if target_skip(record):
                continue
            destination = package_file_path(config, record)
            source_key = package_source_key(record)
            if destination.exists() and destination.stat().st_size > 0:
                report["packages"].append({
                    "identity": identity("packages", record),
                    "source_key": source_key,
                    "path": str(destination),
                    "bytes": destination.stat().st_size,
                    "status": "already_staged",
                })
                report["already_staged_count"] += 1
                continue
            try:
                direct_rest_download_file(
                    source_config, package_download_endpoint(config, record), destination,
                )
            except SystemExit as exc:
                if not config.get("continue_on_package_error", True):
                    raise
                report["failed_packages"].append({
                    "identity": identity("packages", record),
                    "source_key": source_key,
                    "path": str(destination),
                    "error": str(exc),
                })
                report["failed_count"] += 1
                continue
            report["packages"].append({
                "identity": identity("packages", record),
                "source_key": source_key,
                "path": str(destination),
                "bytes": destination.stat().st_size if destination.exists() else 0,
                "status": "downloaded",
            })
            report["downloaded_count"] += 1
        return report

    maybe_switch_tenant(config, "source")
    for record in packages:
        if target_skip(record):
            continue
        destination = package_file_path(config, record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        key = identity("packages", record)
        source_key = package_source_key(record)
        if destination.exists() and destination.stat().st_size > 0:
            report["packages"].append({
                "identity": key,
                "source_key": source_key,
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "status": "already_staged",
            })
            report["already_staged_count"] += 1
            continue
        try:
            run_command([
                "uip", "or", "packages", "download", key,
                "--destination", str(destination), "--output", "json",
            ], capture=True)
        except SystemExit as exc:
            if not config.get("continue_on_package_error", True):
                raise
            report["failed_packages"].append({
                "identity": key,
                "source_key": source_key,
                "path": str(destination),
                "error": str(exc),
            })
            report["failed_count"] += 1
            continue
        report["packages"].append({
            "identity": key,
            "source_key": source_key,
            "path": str(destination),
            "bytes": destination.stat().st_size if destination.exists() else 0,
            "status": "downloaded",
        })
        report["downloaded_count"] += 1
    return report


def stage_libraries(
    config: dict[str, Any],
    discovery_path: str | None = None,
    target_discovery_path: str | None = None,
) -> dict[str, Any]:
    require_target(config, "stage libraries")
    libraries = load_library_records(config, discovery_path)
    source_config = config.get("source", {})
    report: dict[str, Any] = {
        "staged_at": now_utc(),
        "phase": ENTITY_PHASE_BY_ENTITY["libraries"],
        "package_staging_folder": config.get("package_staging_folder", "DownloadedPackages"),
        "target_match_source": None,
        "source_count": len(libraries),
        "target_count": 0,
        "source_library_count": len(libraries),
        "target_library_count": 0,
        "already_in_target_count": 0,
        "already_staged_count": 0,
        "downloaded_count": 0,
        "failed_count": 0,
        "libraries": [],
        "failed_libraries": [],
        "skipped_libraries": [],
    }
    if not libraries:
        return report
    target_discovery_path = target_discovery_path or default_binary_discovery_path(config)
    target_libraries, target_match_source = load_target_binary_records(
        config, "libraries", target_discovery_path,
    )
    target_index = binary_target_index("libraries", target_libraries)
    report["target_match_source"] = target_match_source
    report["target_count"] = len(target_libraries)
    report["target_library_count"] = len(target_libraries)

    def target_skip(record: dict[str, Any]) -> bool:
        match_key = library_match_key(record)
        target_record = target_index.get(match_key)
        if target_record is None:
            return False
        entry = {
            "identity": identity("libraries", record),
            "source_key": library_source_key(record),
            "match_key": list(match_key),
            "target_match": True,
            "target_match_source": target_match_source,
            "reason": "exact_library_id_and_version_already_in_target",
            "target_record": report_safe_record(target_record),
            "status": "already_in_target",
        }
        report["skipped_libraries"].append(entry)
        report["already_in_target_count"] += 1
        return True

    if direct_rest_enabled(source_config):
        for record in libraries:
            if target_skip(record):
                continue
            destination = library_file_path(config, record)
            source_key = library_source_key(record)
            if destination.exists() and destination.stat().st_size > 0:
                report["libraries"].append({
                    "identity": identity("libraries", record),
                    "source_key": source_key,
                    "path": str(destination),
                    "bytes": destination.stat().st_size,
                    "status": "already_staged",
                })
                report["already_staged_count"] += 1
                continue
            try:
                direct_rest_download_file(
                    source_config, library_download_endpoint(config, record), destination,
                )
            except SystemExit as exc:
                if not config.get(
                    "continue_on_library_error",
                    config.get("continue_on_package_error", True),
                ):
                    raise
                report["failed_libraries"].append({
                    "identity": identity("libraries", record),
                    "source_key": source_key,
                    "path": str(destination),
                    "error": str(exc),
                })
                report["failed_count"] += 1
                continue
            report["libraries"].append({
                "identity": identity("libraries", record),
                "source_key": source_key,
                "path": str(destination),
                "bytes": destination.stat().st_size if destination.exists() else 0,
                "status": "downloaded",
            })
            report["downloaded_count"] += 1
        return report

    maybe_switch_tenant(config, "source")
    for record in libraries:
        if target_skip(record):
            continue
        destination = library_file_path(config, record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        key = library_source_key(record)
        if destination.exists() and destination.stat().st_size > 0:
            report["libraries"].append({
                "identity": identity("libraries", record),
                "source_key": key,
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "status": "already_staged",
            })
            report["already_staged_count"] += 1
            continue
        try:
            run_command([
                "uip", "or", "libraries", "download", key,
                "--destination", str(destination), "--output", "json",
            ], capture=True)
        except SystemExit as exc:
            if not config.get(
                "continue_on_library_error",
                config.get("continue_on_package_error", True),
            ):
                raise
            report["failed_libraries"].append({
                "identity": identity("libraries", record),
                "source_key": key,
                "path": str(destination),
                "error": str(exc),
            })
            report["failed_count"] += 1
            continue
        report["libraries"].append({
            "identity": identity("libraries", record),
            "source_key": key,
            "path": str(destination),
            "bytes": destination.stat().st_size if destination.exists() else 0,
            "status": "downloaded",
        })
        report["downloaded_count"] += 1
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
    if entity == "settings" and not setting_is_migratable(record):
        operation = "manual_review"
    action = {
        "entity": entity,
        "identity": key,
        "operation": operation,
        "source_record": record,
        "uip_family": uip_family(entity),
        "phase": ENTITY_PHASE_BY_ENTITY.get(entity, "configuration"),
        "requires_manual_mapping": operation == "manual_review",
        "notes": [],
    }
    if action["operation"] == "manual_review":
        action["notes"].append("Discovery and diff are supported, but automatic apply is not implemented for this entity. Review Cloud mapping, identity dependencies, permissions, and secrets before migration.")
    if entity == "roles" and action["operation"] == "manual_review":
        action["notes"].append("Built-in/static or Mixed roles are not recreated. Custom Tenant/Folder roles can be created automatically; permissions require review if not present in discovery.")
    if entity == "users" and action["operation"] == "manual_review":
        action["notes"].append("User migration uses Identity Service import and is disabled until config.auto_import_users is true. Local On-Prem users cannot be copied as local Cloud users.")
    if entity == "storage_buckets" and action["operation"] == "manual_review":
        action["notes"].append("External storage buckets need provider secrets/credential-store mappings. Built-in Orchestrator buckets can be created automatically.")
    if entity == "packages":
        action["operation"] = "download_upload"
        action["package_staging_folder"] = config.get("package_staging_folder", "DownloadedPackages")
    if entity == "libraries":
        action["operation"] = "download_upload"
        action["package_staging_folder"] = config.get("package_staging_folder", "DownloadedPackages")
        action["notes"].append("Tenant feed library will be downloaded as .nupkg and uploaded to the target tenant feed. Host feed libraries are skipped during planning.")
    if entity == "assets" and is_credential_asset(record):
        action["credential_asset_password_mode"] = "dummy"
        action["dummy_password"] = config.get("dummy_credential_password", "DummyPassword")
        action["notes"].append("Credential asset will be created with dummy password and must be corrected after migration.")
    if entity == "roles" and action["operation"] == "create":
        permissions = role_permission_names(record)
        if permissions:
            action["permissions"] = permissions
            action["notes"].append(
                f"Role is created empty, then granted {len(permissions)} permission(s) by a follow-up update keyed on the new role."
            )
        else:
            action["notes"].append("Discovery returned no permissions for this role; it will be created with none. Expand role permissions at the source or grant them in the target.")
    if entity == "triggers":
        action["trigger_kind"] = trigger_kind(record)
        action["notes"].append(
            f"Planned as a {action['trigger_kind']} trigger. Release, queue, and calendar keys are resolved against the target at apply time, so processes, queues, and calendars must be applied first."
        )
    if entity == "calendars":
        excluded = calendar_excluded_dates(record)
        if excluded:
            action["excluded_date_count"] = len(excluded)
            action["notes"].append(f"{len(excluded)} excluded date(s) are migrated with the calendar.")
    if entity == "credential_stores" and action["operation"] == "create":
        action["notes"].append(
            "Store definition is created through the Cloud API. Provider secrets are not returned by discovery, "
            "so any store needing a credential must be re-authenticated in the target."
        )
    if entity == "queue_items":
        action["notes"].append(
            "Only New-state items are migrated. Re-running apply can duplicate items in a queue that does not enforce unique references."
        )
    if entity == "bucket_files":
        action["notes"].append(
            "File content is streamed source-to-target through pre-signed URIs. External-provider buckets are excluded."
        )
    if entity == "settings" and action["operation"] == "manual_review":
        action["notes"].append("Setting carries a secret or points at the source deployment; it is not copied. Set it deliberately in the target.")
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
        "assets": "uip or assets",
        "queues": "uip or queues",
        "storage_buckets": "uip or buckets",
        "packages": "uip or packages",
        "libraries": "uip or libraries",
        "processes": "uip or processes",
        "calendars": "uip or calendars",
        "triggers": "uip or triggers",
        "webhooks": "uip or webhooks",
        "feeds": "uip or feeds",
        "settings": "uip or settings",
        "bucket_files": "orchestrator REST (bucket read/write URI)",
        "queue_items": "orchestrator REST (BulkAddQueueItems)",
    }[entity]


def make_plan(config: dict[str, Any], source_discovery: str | None = None, target_discovery: str | None = None) -> dict[str, Any]:
    source = normalize_discovery_payload(
        read_json(source_discovery) if source_discovery else discover(config, "source"),
        "source",
    )
    if target_discovery:
        target = normalize_discovery_payload(read_json(target_discovery), "target")
    elif target_absent(config):
        # Source-only assessment: nothing to diff against, so every source record
        # is planned and nothing can be skipped as already existing.
        target = {"entities": {entity: [] for entity in selected_entities(config)}}
    else:
        target = discover(config, "target")
    actions = []
    skipped = []
    manual = []
    for entity in selected_entities(config):
        source_index = build_indexes(source["entities"].get(entity, []), entity)
        target_index = build_indexes(target["entities"].get(entity, []), entity)
        for key, record in sorted(source_index.items()):
            if entity == "roles" and truthy(record.get("IsStatic")):
                skipped.append({
                    "entity": entity, "identity": key, "reason": "built_in_static_role",
                    "source_record": report_safe_record(record),
                })
                continue
            if entity == "libraries" and library_is_host_feed(record):
                skipped.append({
                    "entity": entity, "identity": key, "reason": "host_feed_library",
                    "source_record": report_safe_record(record),
                })
                continue
            if key in target_index:
                skipped.append({
                    "entity": entity, "identity": key, "reason": "already_exists",
                    "source_record": report_safe_record(record),
                })
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
            if entity == "webhooks" and action.get("operation") == "create":
                manual.append({
                    "entity": "webhooks",
                    "identity": key,
                    "reason": "webhook_temporary_signing_secret",
                    "required_action": "Rotate the webhook signing secret in the target. Until then the receiving endpoint cannot verify payloads.",
                })
            if entity == "credential_stores" and action.get("operation") == "create":
                manual.append({
                    "entity": "credential_stores",
                    "identity": key,
                    "reason": "credential_store_secret_not_migratable",
                    "required_action": "Re-authenticate the store in the target: provider secrets and protected configuration are never returned by discovery.",
                })
            if entity == "roles" and action.get("operation") == "create" and not action.get("permissions"):
                manual.append({
                    "entity": "roles",
                    "identity": key,
                    "reason": "role_permissions_unknown",
                    "required_action": "Discovery returned no permissions for this custom role. Grant its permissions in the target.",
                })
    plan = {
        "migration_plan_version": PLAN_VERSION,
        "generated_at": now_utc(),
        "migration_mode": config.get("migration_mode", "lift_and_shift"),
        "target_diffed": not target_absent(config) or bool(target_discovery),
        "entities": selected_entities(config),
        "dependency_order": ENTITY_ORDER,
        "phase_order": list(ENTITY_PHASES),
        "phases": ENTITY_PHASES,
        "actions": actions,
        "skipped": skipped,
        "manual_remediation": manual,
        "source_summary": {entity: len(source["entities"].get(entity, [])) for entity in selected_entities(config)},
        "target_summary": {entity: len(target["entities"].get(entity, [])) for entity in selected_entities(config)},
        # Store only natural keys, never the target's raw inventory. Validation
        # re-discovers the live target and compares this fingerprint immediately
        # before apply, without copying sensitive or volatile server fields into
        # the plan.
        "target_inventory_keys": inventory_keys(target, selected_entities(config)),
        "target_inventory_signature": inventory_signature(target, selected_entities(config)),
        "target_discovery_generated_at": target.get("generated_at", ""),
        "discovery_errors": list(source.get("errors", [])) + list(target.get("errors", [])),
        "scope_exclusions": list(source.get("discovery_notes", [])),
    }
    # Stamp each action with how it will actually be delivered, and run the
    # cross-entity readiness checks, so the plan file carries the same verdict
    # the analysis workbook shows.
    for action in actions:
        detail = action_disposition(action, config)
        action["disposition"] = detail["disposition"]
        action["disposition_reason"] = detail["why"]
        action["operator_action"] = detail["who_does_what"]
        action["operator_action_timing"] = detail["when"]
    plan["readiness"] = readiness_findings(config, plan)
    return plan


def action_status(action: dict[str, Any]) -> str:
    operation = action.get("operation", "")
    entity = action.get("entity", "")
    if operation == "manual_review":
        return "Manual Review"
    if entity in APPLY_SUPPORTED_ENTITIES:
        return "Auto Apply Supported"
    return "Manual Review"


def summarize_notes(action: dict[str, Any]) -> str:
    notes = action.get("notes") or []
    if isinstance(notes, list):
        return " | ".join(str(note) for note in notes)
    return str(notes)


DISPOSITION_AUTOMATIC = "Automatic"
DISPOSITION_HYBRID = "Hybrid — applied, then manual follow-up"
DISPOSITION_PREREQUISITE = "Manual prerequisite — create in target BEFORE apply"
DISPOSITION_MANUAL = "Manual only — no create endpoint"

# Entities with no create endpoint anywhere in the Orchestrator API. Verified
# against the API surface, not inferred from CLI coverage.
NO_CREATE_ENDPOINT_ENTITIES = {"robots", "environments", "feeds"}


def action_disposition(action: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    """Classify one action for the pre-migration report.

    Four outcomes: fully automatic, applied-then-follow-up, blocked until a human
    creates something in the target, or hand-build only.
    """
    entity = str(action.get("entity", ""))
    record = action.get("source_record", {})
    profile = entity_profile(entity)

    if entity in NO_CREATE_ENDPOINT_ENTITIES:
        return {
            "disposition": DISPOSITION_MANUAL,
            "why": "No create endpoint exists in the Orchestrator API for this entity.",
            "who_does_what": profile.get("prerequisite") or "Recreate by hand in the target.",
            "when": "Before cutover",
        }

    if action.get("operation") == "manual_review":
        if entity == "users":
            return {
                "disposition": DISPOSITION_PREREQUISITE,
                "why": "The identity principal is not confirmed in the target, or user import is disabled in config.",
                "who_does_what": "Create or invite the principal in the target organization, then enable user import.",
                "when": "Before apply",
            }
        if entity == "storage_buckets":
            return {
                "disposition": DISPOSITION_PREREQUISITE,
                "why": "External-provider bucket: provider configuration and secrets are not migratable.",
                "who_does_what": "Recreate the bucket and its provider configuration in the target by hand.",
                "when": "Before apply",
            }
        if entity == "roles":
            return {
                "disposition": DISPOSITION_MANUAL,
                "why": "Built-in, static, or non-standard role type — recreating it would cause permission drift.",
                "who_does_what": "Use the target's equivalent built-in role, or design a custom role deliberately.",
                "when": "Before cutover",
            }
        if entity == "libraries":
            return {
                "disposition": DISPOSITION_PREREQUISITE,
                "why": "Host-feed library: shared source infrastructure rather than tenant content.",
                "who_does_what": "Decide whether it becomes a tenant library in the target, and publish it there.",
                "when": "Before apply",
            }
        if entity == "settings":
            return {
                "disposition": DISPOSITION_MANUAL,
                "why": "Setting carries a secret or points at the source deployment.",
                "who_does_what": "Set the value deliberately in the target.",
                "when": "After apply",
            }
        return {
            "disposition": DISPOSITION_MANUAL,
            "why": "Automatic apply is not implemented for this entity.",
            "who_does_what": profile.get("post_action") or "Review and recreate in the target.",
            "when": "Before cutover",
        }

    # Applied automatically, but incomplete on arrival.
    if entity == "assets" and is_credential_asset(record):
        return {
            "disposition": DISPOSITION_HYBRID,
            "why": "Credential passwords are never returned by discovery; the asset is created with a dummy password.",
            "who_does_what": "Set the real password on the target asset.",
            "when": "After apply",
        }
    if entity == "webhooks":
        return {
            "disposition": DISPOSITION_HYBRID,
            "why": "Signing secrets are never returned by discovery; the webhook is created with a temporary secret.",
            "who_does_what": "Rotate the signing secret in the target.",
            "when": "After apply",
        }
    if entity == "credential_stores":
        return {
            "disposition": DISPOSITION_HYBRID,
            "why": "The store definition is created, but its provider secret cannot be migrated.",
            "who_does_what": "Re-authenticate the store against its provider.",
            "when": "After apply",
        }
    if entity == "machines":
        return {
            "disposition": DISPOSITION_HYBRID,
            "why": "The template is created, but machine keys and licence allocation are not migratable.",
            "who_does_what": "Generate keys, register the machine, allocate licences.",
            "when": "After apply",
        }
    if entity == "roles" and not action.get("permissions"):
        return {
            "disposition": DISPOSITION_HYBRID,
            "why": "Discovery returned no permissions for this custom role, so it is created empty.",
            "who_does_what": "Grant the role's permissions in the target.",
            "when": "After apply",
        }
    if entity == "triggers":
        return {
            "disposition": DISPOSITION_HYBRID,
            "why": "Enablement is preserved, so an enabled trigger begins firing jobs on arrival.",
            "who_does_what": "Review the trigger and enable it deliberately.",
            "when": "After apply, before cutover",
        }
    if entity == "queue_items":
        return {
            "disposition": DISPOSITION_HYBRID,
            "why": "Only New-state items migrate; a re-run can duplicate items where unique reference is not enforced.",
            "who_does_what": "Reconcile item counts against the source New-state count.",
            "when": "After apply",
        }
    return {
        "disposition": DISPOSITION_AUTOMATIC,
        "why": "Fully recreated by the tool with no human step.",
        "who_does_what": "—",
        "when": "—",
    }


def readiness_findings(config: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Cross-entity checks that answer 'can this migration run at all yet?'.

    These are found at analysis time rather than at apply time, which is the whole
    point: a blocker discovered halfway through an apply has already half-populated
    the target.
    """
    findings: list[dict[str, Any]] = []
    actions = plan.get("actions", [])
    entities = plan.get("entities", selected_entities(config))
    source_summary = plan.get("source_summary", {})
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        by_entity.setdefault(str(action.get("entity", "")), []).append(action)

    singular = {
        "packages": "package", "libraries": "library", "queue_items": "queue item",
        "bucket_files": "bucket file", "processes": "process", "queues": "queue",
        "calendars": "calendar", "storage_buckets": "storage bucket",
    }

    def plural(count: int, noun: str) -> str:
        return f"{count} {noun}" if count == 1 else f"{count} {noun}s"

    def add(severity: str, area: str, finding: str, impact: str, resolution: str, blocks: bool) -> None:
        # Anything that stops apply is a Blocker regardless of how it was labelled;
        # a row reading "Action required / blocks apply: Yes" invites triage errors.
        findings.append({
            "severity": "Blocker" if blocks else severity,
            "area": area,
            "finding": finding,
            "impact": impact,
            "resolution": resolution,
            "blocks_apply": "Yes" if blocks else "No",
        })

    # A discovery-time failure that continue_on_entity_error swallowed leaves that
    # entity's count at zero, indistinguishable from a tenant that genuinely has
    # none. Surface it here instead of letting it read as a clean, empty family.
    for error in plan.get("discovery_errors", []):
        entity_name = str(error.get("entity", "unknown"))
        side_name = str(error.get("side", "source"))
        add(
            "Blocker", "discovery",
            f"Discovery failed for {side_name} entity '{entity_name}': "
            f"{str(error.get('message', 'unknown error')).rstrip('.')}.",
            f"'{entity_name}' shows as 0 records in this report, which is indistinguishable from a "
            "tenant that genuinely has none. The true count is unknown.",
            f"Re-run discovery for {entity_name} alone to see the full error, fix the underlying cause "
            "(a rejected query, an auth/scope gap, or a transient network issue), then regenerate the plan.",
            True,
        )

    # The declared shape and the resolved scope must agree. A config that says
    # lift-and-shift but names folders (or omits families) migrated less than the
    # operator approved, which is the quietest way to lose data in a cutover.
    mode = str(config.get("migration_mode", "lift_and_shift"))
    declared_folders = [f for f in (config.get("source", {}) or {}).get("folder_paths", []) if str(f)]
    missing_families = [e for e in ENTITY_ORDER if e not in entities]
    if mode == "lift_and_shift":
        if declared_folders:
            add(
                "Blocker", "scope",
                f"Migration mode is lift-and-shift, but folder scope is narrowed to {plural(len(declared_folders), 'folder')}.",
                "Every folder outside that list is silently excluded from a migration the operator approved as complete.",
                "Clear the folder scope for a true lift-and-shift, or change the mode to reflect a targeted migration.",
                True,
            )
        if missing_families:
            add(
                "Blocker", "scope",
                f"Migration mode is lift-and-shift, but {len(missing_families)} entity "
                f"{'family is' if len(missing_families) == 1 else 'families are'} out of scope: "
                f"{', '.join(missing_families)}.",
                "Those families are silently excluded from a migration the operator approved as complete.",
                "Add the missing families, or change the mode to reflect a targeted migration.",
                True,
            )
    elif mode == "folder_subset" and not declared_folders:
        add(
            "Blocker", "scope",
            "Migration mode is folder_subset, but no folder paths are named.",
            "The run would cover every discoverable folder — the opposite of what was approved.",
            "Name the folder paths to migrate, or change the mode to lift_and_shift.",
            True,
        )
    elif mode == "assessment_only":
        add(
            "Information", "scope",
            "Migration mode is assessment_only.",
            "Nothing will be staged or applied; this report is the deliverable.",
            "Change the mode when the operator is ready to migrate.",
            False,
        )
    if not plan.get("target_diffed", True):
        add(
            "Information", "target",
            "No target tenant is configured, so this is a source inventory rather than a diff.",
            "Counts are source-side only. Nothing is marked as already existing, and collisions with "
            "an existing target cannot be detected — every source record appears as work to do.",
            "Re-run the analysis once the target tenant exists to get the true delta.",
            False,
        )

    # Apply refuses a plan that mixes manual review with automatic actions, and
    # there is no per-record exclusion, so a contaminated family blocks wholesale.
    for entity, entity_actions in sorted(by_entity.items()):
        manual = [a for a in entity_actions if a.get("operation") == "manual_review"]
        auto = [a for a in entity_actions if a.get("operation") != "manual_review"]
        if manual and auto:
            add(
                "Blocker", entity,
                f"{len(manual)} of {len(entity_actions)} records need manual review while {len(auto)} are automatic.",
                "Apply refuses the whole plan when any action needs manual review, so none of this family migrates.",
                f"Drop '{entity}' from the entity scope for the automated run and handle those records separately, "
                "or resolve what makes them manual first.",
                True,
            )
        elif manual and not auto:
            add(
                "Action required", entity,
                f"All {plural(len(manual), 'record')} in this family need manual handling.",
                "Nothing in this family migrates automatically.",
                f"Remove '{entity}' from the entity scope, and treat it as hand-build work.",
                True,
            )

    # Credential assets cannot be applied without a target store.
    credential_assets = [
        a for a in by_entity.get("assets", [])
        if a.get("operation") != "manual_review" and is_credential_asset(a.get("source_record", {}))
    ]
    if credential_assets:
        has_mapping = bool(config.get("target_credential_store_key") or config.get("credential_store_key_map"))
        if not has_mapping and plan.get("target_diffed", True):
            add(
                "Blocker", "assets",
                f"{plural(len(credential_assets), 'credential/secret asset')} in scope with no target credential store configured.",
                "Validation fails before apply; no credential asset can be created.",
                "Set the target credential store in config, or migrate credential stores first and map them by name.",
                True,
            )
        if not has_mapping and not plan.get("target_diffed", True):
            add(
                "Action required", "assets",
                f"{plural(len(credential_assets), 'credential asset')} will need a target credential store, which does not exist yet.",
                "Credential assets cannot be applied until a credential store exists in the target and is named in config.",
                "Provision the target, create or migrate a credential store, then name it in config before apply.",
                False,
            )
        add(
            "Action required", "assets",
            f"{plural(len(credential_assets), 'credential asset')} will be created with a dummy password.",
            "Each is a live misconfiguration in the target until an operator sets the real password.",
            "Collect the real passwords before cutover and set them immediately after apply.",
            False,
        )

    # Triggers bind by target key, so their dependencies must be in the same run
    # or already present in the target.
    trigger_actions = by_entity.get("triggers", [])
    if trigger_actions:
        for dependency in ("processes", "queues", "calendars"):
            if dependency not in entities and int(source_summary.get(dependency, 0) or 0) > 0:
                add(
                    "Blocker", "triggers",
                    f"Triggers are in scope but '{dependency}' is not, and the source has {dependency}.",
                    "A trigger resolves its bindings by target key; a missing dependency fails at apply.",
                    f"Add '{dependency}' to the entity scope, or confirm every referenced {dependency[:-1]} already exists in the target.",
                    True,
                )
        enabled = [
            a for a in trigger_actions
            if a.get("operation") != "manual_review"
            and raw_value(a.get("source_record", {}), ["Enabled"]) is not None
            and truthy(a["source_record"].get("Enabled"))
        ]
        if enabled:
            add(
                "Action required", "triggers",
                f"{plural(len(enabled), 'trigger')} enabled at the source will arrive enabled.",
                "The target tenant begins running production workloads as soon as they are applied.",
                "Decide before apply whether to review them first, or disable them at the source for the migration window.",
                False,
            )

    # Content families need their parent in scope.
    for content, parent in (("queue_items", "queues"), ("bucket_files", "storage_buckets")):
        if by_entity.get(content) and parent not in entities:
            add(
                "Blocker", content,
                f"'{content}' is in scope but its parent family '{parent}' is not.",
                "Content cannot be written before the queue or bucket that holds it exists.",
                f"Add '{parent}' to the entity scope, or confirm the parents already exist in the target.",
                True,
            )

    # Binaries are a pre-apply step, not part of analysis.
    for entity in ("packages", "libraries"):
        staged = [a for a in by_entity.get(entity, []) if a.get("operation") == "download_upload"]
        if staged:
            binary_word = "binary" if len(staged) == 1 else "binaries"
            add(
                "Action required", entity,
                f"{len(staged)} {singular.get(entity, entity)} {binary_word} must be downloaded before apply.",
                "Validation fails and every dependent process fails if binaries are not staged.",
                f"Run the {singular.get(entity, entity)} staging step after the analysis is approved.",
                False,
            )

    # Processes bind to a package version.
    if by_entity.get("processes") and "packages" not in entities:
        add(
            "Blocker", "processes",
            "Processes are in scope but 'packages' is not.",
            "A process cannot be created until its package version resolves in the target feed.",
            "Add 'packages' to the entity scope, or confirm every referenced package version already exists in the target.",
            True,
        )

    # A direct REST On-Prem target supports a narrower set.
    if direct_rest_enabled(config.get("target", {})):
        unsupported = sorted({
            str(a.get("entity")) for a in actions
            if a.get("entity") not in DIRECT_REST_APPLY_SUPPORTED_ENTITIES
        })
        if unsupported:
            add(
                "Blocker", "target",
                f"Direct REST target cannot apply: {', '.join(unsupported)}.",
                "Apply refuses the plan.",
                "Narrow the entity scope to what the direct REST target supports.",
                True,
            )

    # Volume is a scheduling decision, not a detail.
    item_count = len(by_entity.get("queue_items", []))
    if item_count:
        add(
            "Information", "queue_items",
            f"{plural(item_count, 'New-state queue item')} will be migrated.",
            "Item volume drives apply duration more than every configuration entity combined.",
            "Confirm the apply window, and enable unique reference on target queues so a re-run cannot duplicate items.",
            False,
        )
    file_actions = by_entity.get("bucket_files", [])
    if file_actions:
        total_bytes = sum(int(a["source_record"].get("Size") or 0) for a in file_actions)
        add(
            "Information", "bucket_files",
            f"{plural(len(file_actions), 'bucket file')} totalling roughly {total_bytes // 1024} KB will be copied.",
            "Every file is a source download plus a target upload.",
            "Confirm the apply window covers the transfer.",
            False,
        )

    # Empty families the operator may have expected to be populated. A family
    # whose discovery actually failed already has its own Blocker above — that
    # explains the zero count; restating it as "genuinely empty?" would mislead.
    failed_entities = {str(e.get("entity", "")) for e in plan.get("discovery_errors", [])}
    for entity in entities:
        if entity in failed_entities:
            continue
        if int(source_summary.get(entity, 0) or 0) == 0:
            add(
                "Information", entity,
                "Source discovery returned no records for this family.",
                "Nothing will migrate. If records were expected, folder scope or External App scopes are wrong.",
                "Confirm this family is genuinely empty at the source before approving.",
                False,
            )

    if not findings:
        add(
            "Information", "readiness",
            "No blockers or prerequisites were detected.",
            "The plan is ready for staging and validation.",
            "Proceed to the approval gate.",
            False,
        )
    severity_order = {"Blocker": 0, "Action required": 1, "Information": 2}
    findings.sort(key=lambda item: (severity_order.get(item["severity"], 3), item["area"]))
    return findings


def plural(count_value: int, noun: str) -> str:
    return f"{count_value} {noun}" if count_value == 1 else f"{count_value} {noun}s"


def not_migrated_items(
    entities: list[str],
    scope_exclusions: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for entity in entities:
        retained = entity_profile(entity)["retained"]
        if retained and retained.strip() not in ("", "-", "\u2014"):
            items.append({
                "area": entity, "retained": retained,
                "reason": "Not writable through the API, or deliberately out of scope",
            })
    # Preserve approved scope exclusions as first-class report rows. A family
    # omitted from the plan must not look like an accidentally empty apply step.
    for exclusion in scope_exclusions or []:
        entity = str(exclusion.get("entity", "")).strip()
        if not entity or entity in entities:
            continue
        profile = entity_profile(entity) if entity in ENTITY_PROFILES else {}
        retained = profile.get("retained") or "Everything"
        reason = str(exclusion.get("reason", "Approved scope exclusion"))
        if "manual review" not in reason.lower():
            reason += " Manual review remains out of band."
        items.append({"area": entity, "retained": retained, "reason": reason})
    items.extend([
        {"area": "history", "retained": "Job, runtime, queue item and audit history", "reason": "Server-owned; no create path"},
        {"area": "identity", "retained": "Machine keys, robot keys, identity records", "reason": "Never returned by discovery"},
        {"area": "metadata", "retained": "Source IDs, timestamps, author fields, licence state", "reason": "Server-owned on create"},
    ])
    return items


APPLY_SEQUENCE_GATES = {
    "assets": "Folders and credential stores applied",
    "packages": "Binaries staged",
    "libraries": "Binaries staged",
    "processes": "Packages uploaded",
    "triggers": "Processes, queues and calendars applied",
    "bucket_files": "Parent bucket applied",
    "queue_items": "Parent queue applied",
}


def apply_sequence_items(entities: list[str], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for position, entity in enumerate(ENTITY_ORDER, start=1):
        in_scope = entity in entities
        items.append({
            "position": position,
            "entity": entity,
            "phase": ENTITY_PHASE_BY_ENTITY.get(entity, "configuration"),
            "phase_label": ENTITY_PHASES.get(
                ENTITY_PHASE_BY_ENTITY.get(entity, "configuration"), {}
            ).get("label", ""),
            "in_scope": in_scope,
            "planned": sum(1 for a in actions if a.get("entity") == entity) if in_scope else 0,
            "gate": APPLY_SEQUENCE_GATES.get(entity, "") if in_scope else "Out of scope — not an apply step",
        })
    return items


def approval_runbook_items() -> list[dict[str, str]]:
    return [
        {"step": "1", "action": "Read the Readiness and Blockers sheet", "purpose": "Every Blocker must be cleared before apply is attempted."},
        {"step": "2", "action": "Complete everything on Manual Prerequisites", "purpose": "These must exist in the target before apply, or apply fails."},
        {"step": "3", "action": "Review Disposition by Entity", "purpose": "Confirm which families are automatic and which must be excluded from the automated run."},
        {"step": "4", "action": "Review Entity Deep Dive", "purpose": "Confirm, per entity, what migrates and what stays behind."},
        {"step": "5", "action": "Review Not Migrated by Design", "purpose": "Confirm nothing expected is silently excluded."},
        {"step": "6", "action": "Approve the scope", "purpose": "This authorises binary staging and validation only - not apply."},
        {"step": "7", "action": "Stage binaries, then validate", "purpose": "Validation runs against the live target."},
        {"step": "8", "action": "Approve apply separately, then canary", "purpose": "Apply a minimal slice, inspect the target, then escalate."},
        {"step": "9", "action": "Work Hybrid Follow-Ups and Post-Migration Remediation", "purpose": "The migration is not complete until these are done."},
    ]


def build_report_model(config: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Everything both report formats need, computed once.

    The Excel workbook and the HTML report are two views of the same
    analysis. They read from this single function so a future change to a
    disposition rule or a verdict threshold updates both at once instead of
    risking the two documents quietly disagreeing with each other.
    """
    actions = plan.get("actions", [])
    skipped = plan.get("skipped", [])
    manual = plan.get("manual_remediation", [])
    entities = plan.get("entities", selected_entities(config))
    source_summary = plan.get("source_summary", {})
    target_summary = plan.get("target_summary", {})
    scope_exclusions = plan.get("scope_exclusions") or []
    findings = plan.get("readiness") or readiness_findings(config, plan)

    dispositions = {id(action): action_disposition(action, config) for action in actions}

    def disp(action: dict[str, Any]) -> dict[str, str]:
        return dispositions[id(action)]

    def count(entity: str, label: str) -> int:
        return sum(1 for a in actions if a.get("entity") == entity and disp(a)["disposition"] == label)

    blockers = [f for f in findings if f["severity"] == "Blocker"]
    todos = [f for f in findings if f["severity"] == "Action required"]
    automatic = [a for a in actions if disp(a)["disposition"] == DISPOSITION_AUTOMATIC]
    hybrid = [a for a in actions if disp(a)["disposition"] == DISPOSITION_HYBRID]
    prereq = [a for a in actions if disp(a)["disposition"] == DISPOSITION_PREREQUISITE]
    manual_only = [a for a in actions if disp(a)["disposition"] == DISPOSITION_MANUAL]
    source_records = sum(int(source_summary.get(e, 0) or 0) for e in entities)
    planned_records = len(actions)
    automation_coverage_percent = round((len(automatic) / planned_records) * 100, 1) if planned_records else 0.0

    target_diffed = plan.get("target_diffed", True)
    # An assessment run has no apply to block, so calling it BLOCKED misreads
    # the situation. The same findings still matter - they are stated as what
    # would block a future apply.
    assessing = (
        str(plan.get("migration_mode", config.get("migration_mode", ""))) == "assessment_only"
        or not target_diffed
    )
    if assessing:
        verdict = (
            f"ASSESSMENT - {plural(len(blockers), 'issue')} would block a future apply"
            if blockers else "ASSESSMENT - no blocking issues found"
        )
    elif blockers:
        verdict = "BLOCKED - resolve blockers before apply"
    elif todos or prereq or hybrid:
        verdict = "READY WITH ACTIONS - prerequisites and follow-ups exist"
    else:
        verdict = "READY"

    return {
        "actions": actions, "skipped": skipped, "manual": manual, "entities": entities,
        "source_summary": source_summary, "target_summary": target_summary, "findings": findings,
        "disp": disp, "count": count,
        "blockers": blockers, "todos": todos,
        "automatic": automatic, "hybrid": hybrid, "prereq": prereq, "manual_only": manual_only,
        "scope_exclusions": scope_exclusions,
        "source_records": source_records, "planned_records": planned_records,
        "automation_coverage_percent": automation_coverage_percent,
        "target_diffed": target_diffed, "assessing": assessing, "verdict": verdict,
    }


def make_analysis_report_rows(config: dict[str, Any], plan: dict[str, Any]) -> list[tuple[str, list[list[Any]]]]:
    model = build_report_model(config, plan)
    actions, skipped, manual, entities = model["actions"], model["skipped"], model["manual"], model["entities"]
    source_summary, target_summary, findings = model["source_summary"], model["target_summary"], model["findings"]
    scope_exclusions = model["scope_exclusions"]
    disp, count = model["disp"], model["count"]
    blockers, todos = model["blockers"], model["todos"]
    automatic, hybrid, prereq, manual_only = model["automatic"], model["hybrid"], model["prereq"], model["manual_only"]
    assessing, verdict, target_diffed = model["assessing"], model["verdict"], model["target_diffed"]
    source_records = model["source_records"]
    planned_records = model["planned_records"]
    automation_coverage_percent = model["automation_coverage_percent"]

    summary_rows = [
        ["Metric", "Value"],
        ["Readiness verdict", verdict],
        ["Migration mode", plan.get("migration_mode", config.get("migration_mode", ""))],
        ["Target comparison", "Diffed against the target tenant" if target_diffed
         else "NONE - source inventory only, no target tenant exists yet"],
        ["Generated at", plan.get("generated_at", "")],
        ["Source tenant", config.get("source", {}).get("tenant", "")
         or "(resolved from the token - tenant-scoped application)"],
        ["Source Orchestrator", config.get("source", {}).get("orchestrator_url", "")],
        ["Target tenant", config.get("target", {}).get("tenant", "") or "(not provisioned)"],
        ["", ""],
        ["Entity families in scope", len(entities)],
        ["Source records discovered", sum(int(source_summary.get(e, 0) or 0) for e in entities)],
        ["Target records already present",
         sum(int(target_summary.get(e, 0) or 0) for e in entities) if target_diffed else "n/a"],
        ["", ""],
        ["Planned actions", len(actions)],
        ["  Automatic - no human step", len(automatic)],
        ["  No-human-intervention coverage of planned actions", f"{automation_coverage_percent:.1f}%"],
        ["  Hybrid - applied, then follow-up", len(hybrid)],
        ["  Manual prerequisite - create before apply", len(prereq)],
        ["  Manual only - no create endpoint", len(manual_only)],
        ["Skipped (already exists / built-in / excluded)", len(skipped)],
        ["", ""],
        ["Blockers that would block a future apply" if assessing else "Blockers to clear before apply", len(blockers)],
        ["Actions required by an operator", len(todos)],
        ["Post-migration remediation items", len(manual)],
        ["", ""],
        ["Approval required before binary staging", "n/a - assessment only" if assessing else "Yes"],
        ["Approval required before apply", "n/a - assessment only" if assessing else "Yes"],
        ["Package/library files downloaded so far", 0],
        ["Target changes made so far", "None - this report is read-only"],
    ]

    readiness_rows = [["Severity", "Area", "Finding", "Impact if ignored", "Resolution", "Blocks apply"]]
    for item in findings:
        readiness_rows.append([
            item["severity"], item["area"], item["finding"],
            item["impact"], item["resolution"], item["blocks_apply"],
        ])

    disposition_rows = [[
        "Entity", "Source", "Target", "Planned", "Automatic", "Hybrid",
        "Manual prerequisite", "Manual only", "Skipped", "Verdict",
    ]]
    for entity in entities:
        entity_actions = [a for a in actions if a.get("entity") == entity]
        auto_n = count(entity, DISPOSITION_AUTOMATIC)
        hybrid_n = count(entity, DISPOSITION_HYBRID)
        prereq_n = count(entity, DISPOSITION_PREREQUISITE)
        manual_n = count(entity, DISPOSITION_MANUAL)
        skipped_n = sum(1 for s in skipped if s.get("entity") == entity)
        if not entity_actions:
            entity_verdict = "Nothing to migrate"
        elif prereq_n:
            entity_verdict = "Needs manual setup before apply"
        elif manual_n and (auto_n or hybrid_n):
            entity_verdict = "Split - automated run must exclude this family"
        elif manual_n:
            entity_verdict = "Hand-build only"
        elif hybrid_n:
            entity_verdict = "Migrates, then needs follow-up"
        else:
            entity_verdict = "Fully automatic"
        disposition_rows.append([
            entity, source_summary.get(entity, 0),
            target_summary.get(entity, 0) if target_diffed else "n/a",
            len(entity_actions), auto_n, hybrid_n, prereq_n, manual_n, skipped_n, entity_verdict,
        ])

    deep_rows = [[
        "Entity", "Scope", "Write path", "Depends on", "Source", "Planned",
        "What migrates", "What stays behind", "Manual prerequisite",
        "Post-apply action", "Principal risk",
    ]]
    for entity in entities:
        profile = entity_profile(entity)
        deep_rows.append([
            entity, profile["scope"], profile["write_path"], profile["depends_on"],
            source_summary.get(entity, 0),
            sum(1 for a in actions if a.get("entity") == entity),
            profile["migrates"], profile["retained"], profile["prerequisite"],
            profile["post_action"], profile["risk"],
        ])

    action_rows = [[
        "Order", "Phase", "Entity", "Identity", "Folder", "Operation",
        "Disposition", "Why", "Who does what", "When", "Notes",
    ]]
    for index, action in enumerate(actions, start=1):
        detail = disp(action)
        phase = ENTITY_PHASE_BY_ENTITY.get(action.get("entity", ""), "configuration")
        phase_label = ENTITY_PHASES.get(phase, {}).get("label", phase)
        action_rows.append([
            index, phase_label, action.get("entity", ""), action.get("identity", ""),
            record_folder(action.get("source_record", {})), action.get("operation", ""),
            detail["disposition"], detail["why"], detail["who_does_what"], detail["when"],
            summarize_notes(action),
        ])

    def scoped_rows(bucket: list[dict[str, Any]]) -> list[list[Any]]:
        rows: list[list[Any]] = [["Entity", "Identity", "Folder", "Why", "Who does what", "When"]]
        for action in bucket:
            detail = disp(action)
            rows.append([
                action.get("entity", ""), action.get("identity", ""),
                record_folder(action.get("source_record", {})),
                detail["why"], detail["who_does_what"], detail["when"],
            ])
        return rows

    excluded_rows = [["Area", "Not migrated", "Reason"]] + [
        [i["area"], i["retained"], i["reason"]]
        for i in not_migrated_items(entities, scope_exclusions)
    ]

    sequence_rows = [["Order", "Phase", "Entity", "In scope", "Planned actions", "Gate before this step"]] + [
        [i["position"], i["phase_label"], i["entity"], "Yes" if i["in_scope"] else "No", i["planned"], i["gate"]]
        for i in apply_sequence_items(entities, actions)
    ]

    runbook_rows = [["Step", "Decision or action", "Purpose"]] + [
        [i["step"], i["action"], i["purpose"]] for i in approval_runbook_items()
    ]

    return [
        ("Executive Summary", summary_rows),
        ("Readiness and Blockers", readiness_rows),
        ("Disposition by Entity", disposition_rows),
        ("Entity Deep Dive", deep_rows),
        ("Planned Actions", action_rows),
        ("Manual Prerequisites", scoped_rows(prereq)),
        ("Hybrid Follow-Ups", scoped_rows(hybrid)),
        ("Manual Only", scoped_rows(manual_only)),
        ("Skipped", [["Entity", "Identity", "Reason"]] + ([
            [i.get("entity", ""), i.get("identity", ""), i.get("reason", "")] for i in skipped
        ] if target_diffed else [[
            "n/a", "n/a",
            "No target tenant configured - nothing can be detected as already existing. "
            "Re-run once the target exists to get the true delta.",
        ]] + [
            [i.get("entity", ""), i.get("identity", ""), i.get("reason", "")] for i in skipped
        ])),
        ("Not Migrated by Design", excluded_rows),
        ("Post-Migration Remediation", [["Entity", "Identity", "Reason", "Required action"]] + [
            [i.get("entity", ""), i.get("identity", ""), i.get("reason", ""), i.get("required_action", "")]
            for i in manual
        ]),
        ("Apply Sequence", sequence_rows),
        ("Approval Runbook", runbook_rows),
    ]


def make_entity_analysis_rows(config: dict[str, Any], plan: dict[str, Any], entity: str) -> list[list[Any]]:
    """Build one operator-facing tab for one entity family.

    The tab is deliberately record-oriented: an operator can filter by
    disposition, operation, identity, folder, or status without opening the
    machine-readable plan. Source payloads are rendered through the report
    redaction helper so the workbook never becomes a secret store.
    """
    model = build_report_model(config, plan)
    actions = [a for a in model["actions"] if a.get("entity") == entity]
    skipped = [s for s in model["skipped"] if s.get("entity") == entity]
    disp = model["disp"]
    source_count = model["source_summary"].get(entity, 0)
    target_count = model["target_summary"].get(entity, 0) if model["target_diffed"] else "n/a"
    rows: list[list[Any]] = [[
        "Record type", "Identity", "Folder", "Operation", "Disposition", "Status",
        "Why / reason", "Operator action", "When", "Notes", "Source data (sanitized)",
    ]]
    for action in actions:
        detail = disp(action)
        rows.append([
            "Planned", action.get("identity", ""), record_folder(action.get("source_record", {})),
            action.get("operation", ""), detail["disposition"],
            f"Planned; source={source_count}; target={target_count}", detail["why"],
            detail["who_does_what"], detail["when"], summarize_notes(action),
            report_record_text(action.get("source_record", {})),
        ])
    for item in skipped:
        rows.append([
            "Skipped", item.get("identity", ""), record_folder(item.get("source_record", {})),
            "skip", "Skipped",
            f"Not planned; source={source_count}; target={target_count}",
            item.get("reason", ""), "No action", "Before apply", "",
            report_record_text(item.get("source_record", {})),
        ])
    if not actions and not skipped:
        rows.append([
            "Scope", entity, "", "", "Out of scope or empty", "No records in this run",
            "This family has no planned or skipped records in the selected scope.",
            "Confirm scope if records were expected.", "Review", "", "",
        ])
    return rows


def make_workbook_readme_rows(kind: str, config: dict[str, Any], entities: list[str]) -> list[list[Any]]:
    """Explain the operator-facing workbook without requiring JSON knowledge."""
    title = "Migration analysis workbook" if kind == "analysis" else "Migration apply audit workbook"
    return [
        ["Topic", "Guidance"],
        ["Purpose", title],
        ["How to use", "Start with the Summary/Executive Summary tab, then open the per-entity tabs to filter individual records."],
        ["Per-entity tabs", "; ".join(entities) if entities else "No entity families selected"],
        ["Analysis tabs", "Entity tabs show planned and skipped records, disposition, reason, operator action, and sanitized source data." if kind == "analysis" else "Apply tabs show one row per planned logical item, outcome, retries, and sanitized source data."],
        ["Sensitive values", "Report payloads are sanitized. Passwords, tokens, API keys, connection strings, credential usernames/values, and provider configuration are shown as [REDACTED]."],
        ["Machine state", "JSON snapshots, plans, probe results, and stage checkpoints are kept under .migration-state beside the config. They are for engine resume/validation, not operator review."],
        ["Scope", f"Source tenant: {config.get('source', {}).get('tenant', '') or 'not specified'}; Target tenant: {config.get('target', {}).get('tenant', '') or 'not specified'}"],
    ]


def write_analysis_report(config: dict[str, Any], plan: dict[str, Any], xlsx_path: str | Path) -> None:
    entities = list(plan.get("entities", selected_entities(config)))
    sheets = [("Read Me", make_workbook_readme_rows("analysis", config, entities))]
    sheets.extend(make_analysis_report_rows(config, plan))
    for entity in entities:
        sheets.append((f"Entity - {entity}", make_entity_analysis_rows(config, plan, entity)))
    write_xlsx(xlsx_path, sheets)


def apply_attempt_note(item: dict[str, Any]) -> str:
    parts: list[str] = []
    if item.get("attempts") not in (None, ""):
        parts.append(f"attempts={item['attempts']}")
    if item.get("max_attempts") not in (None, ""):
        parts.append(f"max_attempts={item['max_attempts']}")
    if item.get("batch_number") not in (None, ""):
        parts.append(f"batch={item['batch_number']}")
    if item.get("bulk_batch_size") not in (None, ""):
        parts.append(f"bulk_batch_size={item['bulk_batch_size']}")
    if item.get("fallback_from_bulk"):
        parts.append("isolated_after_bulk_failure")
    return "; ".join(parts)


def normalize_apply_outcomes(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn either apply-result shape - uip/Cloud ('commands'/'failures') or
    direct_rest ('actions'/'errors') - into one common row shape for the audit report."""
    rows: list[dict[str, Any]] = []

    def with_attempt_note(item: dict[str, Any], comment: Any) -> str:
        base = xlsx_text(comment)
        note = apply_attempt_note(item)
        return f"{base}; {note}" if base and note else (note or base)

    if "actions" in results or "errors" in results:
        for item in results.get("actions", []):
            status = item.get("status", "")
            outcome = "Skipped - already exists" if status == "already_exists" else "Success"
            rows.append({
                "entity": item.get("entity", ""),
                "identity": item.get("identity", ""),
                "stage": "rest",
                "outcome": outcome,
                "comment": with_attempt_note(item, status),
            })
        for item in results.get("errors", []):
            rows.append({
                "entity": item.get("entity", ""),
                "identity": item.get("identity", ""),
                "stage": "rest",
                "outcome": "Failed",
                "comment": with_attempt_note(item, item.get("error", "")),
            })
    else:
        for item in results.get("commands", []):
            command = item.get("command")
            command_text = " ".join(command) if isinstance(command, list) else xlsx_text(command)
            rows.append({
                "entity": item.get("entity", ""),
                "identity": item.get("identity", ""),
                "stage": item.get("stage", ""),
                "outcome": "Success",
                "comment": with_attempt_note(item, command_text),
            })
        for item in results.get("failures", []):
            command = item.get("command")
            command_text = " ".join(command) if isinstance(command, list) else xlsx_text(command)
            comment = item.get("reason") or command_text
            rows.append({
                "entity": item.get("entity", ""),
                "identity": item.get("identity", ""),
                "stage": item.get("stage", ""),
                "outcome": "Failed",
                "comment": with_attempt_note(item, comment),
            })
    return rows


def make_apply_report_rows(config: dict[str, Any], plan: dict[str, Any], results: dict[str, Any]) -> list[tuple[str, list[list[Any]]]]:
    outcomes = normalize_apply_outcomes(results)
    attempted_identities: set[tuple[str, str]] = {(row["entity"], row["identity"]) for row in outcomes}

    per_entity: dict[str, dict[str, int]] = {}
    for row in outcomes:
        bucket = per_entity.setdefault(row["entity"], {"planned": 0, "success": 0, "failed": 0})
        if row["outcome"] == "Failed":
            bucket["failed"] += 1
        else:
            bucket["success"] += 1

    plan_actions = plan.get("actions", [])
    for action in plan_actions:
        bucket = per_entity.setdefault(action.get("entity", ""), {"planned": 0, "success": 0, "failed": 0})
        bucket["planned"] += 1

    entity_order = list(plan.get("dependency_order") or [])
    ordered_entities = [e for e in entity_order if e in per_entity] + [e for e in per_entity if e not in entity_order]

    summary_rows: list[list[Any]] = [["Entity", "Planned", "Succeeded", "Failed", "Not Attempted This Run"]]
    total_planned = total_success = total_failed = total_not_attempted = 0
    for entity in ordered_entities:
        bucket = per_entity[entity]
        not_attempted = max(0, bucket["planned"] - bucket["success"] - bucket["failed"])
        summary_rows.append([entity, bucket["planned"], bucket["success"], bucket["failed"], not_attempted])
        total_planned += bucket["planned"]
        total_success += bucket["success"]
        total_failed += bucket["failed"]
        total_not_attempted += not_attempted
    summary_rows.append(["TOTAL", total_planned, total_success, total_failed, total_not_attempted])
    summary_rows.append(["", "", "", "", ""])
    summary_rows.append([
        "Note", "Queue items are sent in bulk per (folder, queue) batch, but each item has its own Details/Failures row and its own apply outcome. "
        "If a bulk call fails, each item is attempted once individually so only items still failing in that isolated call are marked failed.",
        "", "", "",
    ])

    detail_rows: list[list[Any]] = [["Entity", "Identity", "Stage", "Outcome", "Comment"]]
    failure_rows: list[list[Any]] = [["Entity", "Identity", "Stage", "Comment"]]
    for row in outcomes:
        detail_rows.append([row["entity"], row["identity"], row["stage"], row["outcome"], row["comment"]])
        if row["outcome"] == "Failed":
            failure_rows.append([row["entity"], row["identity"], row["stage"], row["comment"]])

    manual_rows: list[list[Any]] = [["Entity", "Identity", "Reason", "Required Action"]]
    for item in results.get("manual_remediation", []) or []:
        manual_rows.append([
            item.get("entity", ""), item.get("identity", ""),
            item.get("reason", ""), item.get("required_action", ""),
        ])

    not_attempted_rows: list[list[Any]] = [["Entity", "Identity", "Note"]]
    for action in plan_actions:
        entity = action.get("entity", "")
        identity_value = action.get("identity", "")
        if entity == "queue_items":
            continue  # reconciled by count only - see the Summary sheet's note
        if (entity, identity_value) not in attempted_identities:
            not_attempted_rows.append([entity, identity_value, "Not reached this run - beyond a canary --max-actions limit, or an earlier dependency failed"])

    return [
        ("Summary", summary_rows),
        ("Details", detail_rows),
        ("Failures", failure_rows),
        ("Manual Remediation", manual_rows),
        ("Not Attempted", not_attempted_rows),
    ]


def make_entity_apply_rows(config: dict[str, Any], plan: dict[str, Any], results: dict[str, Any], entity: str) -> list[list[Any]]:
    """Build a per-entity audit tab with one row per logical planned item."""
    outcomes = normalize_apply_outcomes(results)
    by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for outcome in outcomes:
        by_identity.setdefault((outcome["entity"], outcome["identity"]), []).append(outcome)

    rows: list[list[Any]] = [[
        "Record type", "Identity", "Folder", "Operation", "Disposition",
        "Outcome", "Stage(s)", "Attempts / batches", "Comment", "Source data (sanitized)",
    ]]
    entity_actions = [action for action in plan.get("actions", []) if action.get("entity") == entity]
    for action in entity_actions:
        identity = xlsx_text(action.get("identity", ""))
        matched = by_identity.get((entity, identity), [])
        if not matched:
            outcome = "Not attempted"
            stages = ""
            attempts = ""
            comment = "Not reached this run - beyond a canary limit, or an earlier dependency failed."
        else:
            outcome = "Failed" if any(item.get("outcome") == "Failed" for item in matched) else "Success"
            stages = "; ".join(dict.fromkeys(xlsx_text(item.get("stage", "")) for item in matched if item.get("stage")))
            attempts = "; ".join(dict.fromkeys(
                apply_attempt_note(item) for item in matched if apply_attempt_note(item)
            ))
            comment = " | ".join(dict.fromkeys(
                xlsx_text(item.get("comment", "")) for item in matched if item.get("comment")
            ))
        rows.append([
            "Planned", identity, record_folder(action.get("source_record", {})),
            action.get("operation", ""), action.get("disposition", ""), outcome,
            stages, attempts, comment, report_record_text(action.get("source_record", {})),
        ])
    if not entity_actions:
        rows.append([
            "Scope", entity, "", "", "", "No records", "", "",
            "This entity family had no planned actions in this run.", "",
        ])
    return rows


def write_apply_report(config: dict[str, Any], plan: dict[str, Any], results: dict[str, Any], xlsx_path: str | Path) -> None:
    entities = list(plan.get("entities") or selected_entities(config))
    sheets = [("Read Me", make_workbook_readme_rows("apply", config, entities))]
    sheets.extend(make_apply_report_rows(config, plan, results))
    for entity in entities:
        sheets.append((f"Apply - {entity}", make_entity_apply_rows(config, plan, results, entity)))
    write_xlsx(xlsx_path, sheets)


REPORT_CSS = '''
:root{
  --ground:#f6f7fb; --surface:#ffffff; --surface-2:#eef1f7;
  --ink:#161a22; --muted:#5b6472; --line:#dfe3ea;
  --accent:#1f5fbf; --accent-soft:#e7effb;
  --ok:#1c6b47; --ok-soft:#e8f4ee;
  --hybrid:#0e7c86; --hybrid-soft:#e4f4f5;
  --warn:#b26a00; --warn-soft:#fdf4e3;
  --stop:#b3261e; --stop-soft:#fdeceb;
  --shadow:0 1px 2px rgba(20,24,31,.06), 0 8px 24px rgba(20,24,31,.06);
}
@media (prefers-color-scheme: dark){
  :root{
    --ground:#0e1116; --surface:#161b22; --surface-2:#1d242e;
    --ink:#e6eaf0; --muted:#98a2b3; --line:#263040;
    --accent:#6ba4ff; --accent-soft:#132132;
    --ok:#4fd18b; --ok-soft:#11241b;
    --hybrid:#5fd6df; --hybrid-soft:#0f2426;
    --warn:#e0a030; --warn-soft:#2a2010;
    --stop:#ff6b5e; --stop-soft:#2c1512;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35);
  }
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
@media (prefers-reduced-motion: reduce){ html{scroll-behavior:auto} }
body{
  margin:0; background:var(--ground); color:var(--ink);
  font:400 15px/1.6 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}
.mono{font-family:ui-monospace,"Cascadia Code","SFMono-Regular",Consolas,"Liberation Mono",monospace}
.wrap{max-width:960px;margin:0 auto;padding:0 20px 80px}

header.report-header{padding:36px 0 20px;border-bottom:1px solid var(--line)}
.eyebrow{font:600 11px/1 ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
h1{margin:8px 0 4px;font-size:28px;font-weight:700;letter-spacing:-.01em}
.meta-line{color:var(--muted);font-size:13.5px;margin:0 0 18px}
.meta-line b{color:var(--ink);font-weight:600}

.verdict{display:flex;align-items:center;gap:14px;padding:16px 18px;border-radius:12px;border:1px solid var(--line);box-shadow:var(--shadow);flex-wrap:wrap}
.verdict .dot{width:12px;height:12px;border-radius:50%;flex:none}
.verdict-text{font-size:17px;font-weight:700}
.v-ready .dot,.v-assess-clear .dot{background:var(--ok)}
.v-actions .dot{background:var(--warn)}
.v-blocked .dot,.v-assess-blocked .dot{background:var(--stop)}
.v-ready,.v-assess-clear{background:var(--ok-soft);border-color:color-mix(in srgb,var(--ok) 35%,transparent)}
.v-actions{background:var(--warn-soft);border-color:color-mix(in srgb,var(--warn) 35%,transparent)}
.v-blocked,.v-assess-blocked{background:var(--stop-soft);border-color:color-mix(in srgb,var(--stop) 35%,transparent)}

.printbtn{margin-left:auto;font:600 12.5px/1 -apple-system,sans-serif;cursor:pointer;background:var(--surface);color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 34%,transparent);border-radius:8px;padding:8px 12px}
@media (prefers-reduced-motion:no-preference){ .printbtn{transition:filter .15s ease} .printbtn:hover{filter:brightness(1.08)} }

nav.toc{display:flex;gap:6px;flex-wrap:wrap;padding:14px 0;border-bottom:1px solid var(--line);margin-bottom:8px;position:sticky;top:0;background:var(--ground);z-index:2}
nav.toc a{font:500 12px/1 ui-monospace,monospace;text-decoration:none;color:var(--muted);padding:6px 9px;border-radius:6px;border:1px solid var(--line)}
nav.toc a:hover{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 30%,transparent)}
.tabs{margin-top:20px}
.tab-list{display:flex;gap:6px;flex-wrap:wrap;padding:8px;border:1px solid var(--line);border-radius:12px;background:var(--surface);position:sticky;top:10px;z-index:2;box-shadow:var(--shadow)}
.tab-button{display:inline-block;border:1px solid transparent;border-radius:8px;background:transparent;color:var(--muted);font:600 12px/1.2 ui-monospace,monospace;padding:10px 12px;text-decoration:none}
.tab-button:hover,.tab-button:focus-visible{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 30%,transparent)}
.tab-panel{display:block;padding-top:8px;scroll-margin-top:90px}

section{padding:34px 0;border-bottom:1px solid var(--line)}
section:last-of-type{border-bottom:none}
section h2{margin:0 0 4px;font-size:20px;font-weight:700;letter-spacing:-.01em}
.section-note{margin:0 0 20px;color:var(--muted);font-size:13.5px;max-width:70ch}
.section-note b{color:var(--ink)}

.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 14px 12px;display:flex;flex-direction:column;gap:4px;box-shadow:var(--shadow)}
.tile-value{font:700 24px/1 ui-monospace,monospace;letter-spacing:-.02em}
.tile-label{font-size:12px;color:var(--muted)}
.tile.t-auto .tile-value{color:var(--ok)}
.tile.t-hybrid .tile-value{color:var(--hybrid)}
.tile.t-prereq .tile-value{color:var(--warn)}
.tile.t-manual .tile-value{color:var(--stop)}
.tile.t-blocker .tile-value{color:var(--stop)}

.finding{border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:10px;background:var(--surface);border-left-width:4px;break-inside:avoid}
.finding.sev-blocker{border-left-color:var(--stop)}
.finding.sev-action{border-left-color:var(--warn)}
.finding.sev-info{border-left-color:var(--muted)}
.finding-head{display:flex;gap:10px;align-items:center;margin-bottom:6px;flex-wrap:wrap}
.pill{font:600 10.5px/1 ui-monospace,monospace;text-transform:uppercase;letter-spacing:.04em;padding:4px 7px;border-radius:5px}
.sev-blocker .pill{background:var(--stop-soft);color:var(--stop)}
.sev-action .pill{background:var(--warn-soft);color:var(--warn)}
.sev-info .pill{background:var(--surface-2);color:var(--muted)}
.area{font:500 12.5px/1 ui-monospace,monospace;color:var(--muted)}
.finding-text{margin:0 0 6px;font-weight:600}
.finding-impact,.finding-resolution{margin:0 0 4px;font-size:13.5px;color:var(--muted)}
.finding-resolution strong,.finding-impact strong{color:var(--ink)}

table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font:600 11px/1 ui-monospace,monospace;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
td.num{font-family:ui-monospace,monospace;text-align:right}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
.table-wrap table{margin:0}

.bar{display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--surface-2);min-width:110px}
.bar span{display:block}
.bar .t-auto{background:var(--ok)}
.bar .t-hybrid{background:var(--hybrid)}
.bar .t-prereq{background:var(--warn)}
.bar .t-manual{background:var(--stop)}
.bar .t-skip{background:var(--muted);opacity:.5}
.bar .t-empty{background:var(--surface-2)}
.bar-caption{font:400 11px/1.4 ui-monospace,monospace;color:var(--muted);margin-top:5px}

.entity-card{border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:12px;background:var(--surface);break-inside:avoid}
.entity-card header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;gap:10px;flex-wrap:wrap}
.entity-name{font-weight:700;font-size:14.5px}
.entity-counts{font-size:12px;color:var(--muted)}
.entity-meta{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:10px;padding-bottom:10px;border-bottom:1px dashed var(--line)}
.kv{display:flex;flex-direction:column;gap:2px}
.kv .k{font:600 10px/1 ui-monospace,monospace;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.kv .v{font-size:12.5px}
.entity-card p{margin:0 0 6px;font-size:13.5px}
.entity-card .migrates{color:var(--ok)}
.entity-card .retained{color:var(--muted)}
.entity-card .risk{color:var(--warn)}

.checklist{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.check-item{display:flex;gap:10px;border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--surface);break-inside:avoid}
.box{width:16px;height:16px;border:2px solid var(--muted);border-radius:4px;flex:none;margin-top:2px}
.check-head{display:flex;gap:8px;align-items:center;margin-bottom:4px;flex-wrap:wrap}
.tag{font:500 10.5px/1 ui-monospace,monospace;background:var(--surface-2);color:var(--muted);padding:3px 6px;border-radius:5px}
.check-item .why{margin:0 0 4px;color:var(--muted);font-size:13px}
.check-item .who{margin:0;font-size:13px}
.empty{color:var(--muted);font-style:italic;font-size:13.5px}

ul.sequence{list-style:none;margin:0;padding:0;position:relative}
ul.sequence::before{content:"";position:absolute;left:15px;top:6px;bottom:6px;width:2px;background:var(--line)}
.seq-item{display:flex;align-items:center;gap:12px;padding:7px 0;position:relative}
.seq-n{width:30px;height:30px;border-radius:50%;background:var(--surface);border:2px solid var(--line);display:flex;align-items:center;justify-content:center;font:600 11px ui-monospace,monospace;color:var(--muted);flex:none;z-index:1}
.seq-item.in .seq-n{border-color:var(--accent);color:var(--accent)}
.seq-item.out{opacity:.45}
.seq-name{font-weight:600;min-width:150px}
.seq-meta{color:var(--muted);font-size:12.5px}

ul.runbook{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.runbook-item{display:flex;gap:12px}
.rb-n{width:26px;height:26px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:flex;align-items:center;justify-content:center;font:700 12px ui-monospace,monospace;flex:none}
.rb-action{margin:0;font-weight:600;font-size:13.5px}
.rb-purpose{margin:2px 0 0;color:var(--muted);font-size:13px}

footer.report-footer{padding:26px 0 40px;color:var(--muted);font-size:12.5px;max-width:70ch}
footer.report-footer p{margin:0 0 6px}

@media print{
  :root{--ground:#ffffff;--surface:#ffffff;--surface-2:#f4f5f7;--ink:#111318;--muted:#4a5160;--line:#d7dbe2}
  body{background:#fff}
  .printbtn, .tab-list{display:none}
  .tab-panel{display:block !important}
  section{break-inside:avoid-page;border-bottom:1px solid #e3e6ec}
  .finding,.entity-card,.check-item{break-inside:avoid}
  a{color:inherit;text-decoration:none}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
'''


EFFORT_REPORT_CSS = """
:root{--bg:#f6f8fb;--surface:#fff;--ink:#17202b;--muted:#5b6878;--line:#dbe2ea;--accent:#245db1;--accent-soft:#e9f0fb;--ok:#177245;--ok-soft:#e8f5ee;--human:#087e87;--human-soft:#e5f5f6;--warn:#a56100;--warn-soft:#fff4df;--stop:#b3261e;--stop-soft:#fdeceb;--shadow:0 1px 2px rgba(20,30,45,.05),0 5px 18px rgba(20,30,45,.06)}
@media(prefers-color-scheme:dark){:root{--bg:#0e1319;--surface:#171e27;--ink:#e7edf5;--muted:#9aa8b8;--line:#2b3847;--accent:#79adff;--accent-soft:#172a45;--ok:#56d494;--ok-soft:#10271b;--human:#68dce3;--human-soft:#102a2d;--warn:#e8ae50;--warn-soft:#2e2413;--stop:#ff746a;--stop-soft:#311917;--shadow:0 1px 2px rgba(0,0,0,.35),0 5px 18px rgba(0,0,0,.3)}}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}.wrap{max-width:1180px;margin:auto;padding:0 22px 60px}h1{margin:5px 0;font-size:29px;letter-spacing:-.02em}h2{margin:0 0 5px;font-size:21px;letter-spacing:-.01em}h3{margin:0 0 7px;font-size:15px}.eyebrow,.mono,th{font-family:ui-monospace,"Cascadia Code",Consolas,monospace}.eyebrow{font-size:11px;letter-spacing:.11em;text-transform:uppercase;color:var(--muted)}.meta{margin:0;color:var(--muted);font-size:13px}.meta b{color:var(--ink)}header{padding:34px 0 22px;border-bottom:1px solid var(--line)}.verdict{margin-top:16px;padding:14px 16px;border:1px solid var(--line);border-radius:11px;background:var(--surface);box-shadow:var(--shadow)}.verdict strong{font-size:17px}.notice{margin:16px 0 0;padding:12px 14px;border-left:4px solid var(--accent);background:var(--accent-soft);border-radius:7px;color:var(--ink)}nav{display:flex;gap:7px;flex-wrap:wrap;padding:15px 0;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:3}nav a{color:var(--muted);text-decoration:none;border:1px solid var(--line);background:var(--surface);padding:7px 10px;border-radius:7px;font:600 11px ui-monospace,monospace}nav a:hover,nav a:focus{color:var(--accent);border-color:var(--accent)}section.panel{padding:30px 0;border-bottom:1px solid var(--line);scroll-margin-top:75px}.subnote{margin:0 0 16px;color:var(--muted);max-width:90ch}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px}.metric{background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:13px;box-shadow:var(--shadow)}.metric .value{display:block;font:700 24px/1.1 ui-monospace,monospace}.metric .label{display:block;margin-top:5px;color:var(--muted);font-size:12px}.metric.ok .value{color:var(--ok)}.metric.human .value{color:var(--human)}.metric.warn .value{color:var(--warn)}.metric.stop .value{color:var(--stop)}.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-top:16px}.card{background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:14px;box-shadow:var(--shadow)}.card p{margin:5px 0;color:var(--muted)}.table-wrap{overflow:auto;background:var(--surface);border:1px solid var(--line);border-radius:9px}table{width:100%;border-collapse:collapse;min-width:760px}th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:10px;text-transform:uppercase;letter-spacing:.045em;color:var(--muted);white-space:nowrap}td.num{text-align:right;font-family:ui-monospace,monospace;white-space:nowrap}tr:last-child td{border-bottom:0}.badge{display:inline-block;padding:3px 6px;border-radius:5px;font:600 10px ui-monospace,monospace;white-space:nowrap}.auto{background:var(--ok-soft);color:var(--ok)}.hybrid{background:var(--human-soft);color:var(--human)}.prereq{background:var(--warn-soft);color:var(--warn)}.manual,.blocker{background:var(--stop-soft);color:var(--stop)}.skip{background:var(--accent-soft);color:var(--accent)}.empty{padding:16px;color:var(--muted);font-style:italic}.small{font-size:12px;color:var(--muted)}.estimate{min-width:130px;color:var(--muted);font-style:italic}.risk{color:var(--warn)}.oktext{color:var(--ok)}.humantext{color:var(--human)}.blocktext{color:var(--stop)}.sequence{margin:0;padding:0;list-style:none}.sequence li{display:flex;gap:11px;padding:7px 0;border-bottom:1px solid var(--line)}.sequence .n{width:26px;height:26px;display:grid;place-items:center;border:1px solid var(--line);border-radius:50%;font:600 11px ui-monospace,monospace;flex:none}.sequence .out{opacity:.5}.check{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--surface)}.check strong{display:block}.check span{color:var(--muted);font-size:13px}footer{padding:24px 0;color:var(--muted);font-size:12px}.review-workspace{background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:14px;box-shadow:var(--shadow)}.review-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.review-toolbar button{border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--accent);padding:7px 10px;font:600 11px ui-monospace,monospace;cursor:pointer}.review-toolbar button:hover,.review-toolbar button:focus{border-color:var(--accent)}.review-state{color:var(--muted);font-size:12px;margin-left:auto}.review-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.review-field{display:flex;flex-direction:column;gap:4px}.review-field label,.review-inline label,.review-comment label{font:600 10px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}.review-field input,.review-field select,.review-field textarea,.review-inline input,.review-inline select,.review-comment textarea{width:100%;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);padding:7px 8px;font:13px -apple-system,"Segoe UI",Roboto,Arial,sans-serif}.review-field textarea{min-height:72px;resize:vertical}.review-summary{margin-top:10px}.review-summary textarea{min-height:82px}.review-details{min-width:230px}.review-details summary{cursor:pointer;color:var(--accent);font:600 11px ui-monospace,monospace;list-style-position:inside}.review-details[open] summary{margin-bottom:8px}.review-inline{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:7px 0}.review-inline label{display:flex;flex-direction:column;gap:3px}.review-inline input,.review-inline select{font-size:12px;padding:6px}.review-comment textarea{min-height:64px;resize:vertical;font-size:12px}.review-hint{margin:8px 0 0;color:var(--muted);font-size:12px}.review-only-print{display:none}@media print{nav{display:none}section.panel{break-inside:auto}.card,.metric,.table-wrap,.review-workspace{box-shadow:none}.review-toolbar{display:none}.review-only-print{display:block}.review-details{display:block}.review-details summary{display:none}.review-details .review-inline,.review-details .review-comment{display:block;margin:4px 0}.review-details input,.review-details select,.review-details textarea{border:0;padding:0;background:transparent;resize:none}a{color:inherit;text-decoration:none}}
"""


REVIEW_UI_SCRIPT = r'''<script>
(function () {
  const root = document.querySelector('[data-review-scope]');
  if (!root) return;
  const scope = root.dataset.reviewScope || 'default';
  const storageKey = 'uipath-migration-review-v1:' + scope;
  const status = document.getElementById('review-save-state');
  const importFile = document.getElementById('review-import-file');
  let autosaveTimer;

  function setStatus(message) {
    if (status) status.textContent = message;
  }

  function fields() {
    return Array.from(root.querySelectorAll('[data-review-field]'));
  }

  function collect() {
    const values = {};
    fields().forEach(function (field) {
      values[field.dataset.reviewField] = field.value;
    });
    return {
      schema_version: 1,
      scope: scope,
      updated_at: new Date().toISOString(),
      values: values
    };
  }

  function applyReview(payload) {
    if (!payload || payload.scope !== scope || !payload.values) {
      setStatus('Review file belongs to a different report scope.');
      return false;
    }
    fields().forEach(function (field) {
      const value = payload.values[field.dataset.reviewField];
      if (value !== undefined) field.value = value;
    });
    setStatus('Review loaded from ' + (payload.updated_at || 'file') + '.');
    return true;
  }

  function saveLocal(message) {
    try {
      localStorage.setItem(storageKey, JSON.stringify(collect()));
      setStatus(message || ('Saved in this browser at ' + new Date().toLocaleTimeString() + '.'));
    } catch (error) {
      setStatus('Browser storage is unavailable; export the review JSON to preserve notes.');
    }
  }

  function loadLocal() {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved && applyReview(JSON.parse(saved))) return;
      setStatus('No saved review for this report yet.');
    } catch (error) {
      setStatus('Saved review could not be loaded.');
    }
  }

  function exportReview() {
    const blob = new Blob([JSON.stringify(collect(), null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'migration-review-' + scope + '.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setStatus('Review JSON exported.');
  }

  function scheduleAutosave() {
    clearTimeout(autosaveTimer);
    setStatus('Unsaved changes; saving locally...');
    autosaveTimer = setTimeout(function () { saveLocal('Auto-saved in this browser.'); }, 500);
  }

  root.addEventListener('input', scheduleAutosave);
  root.addEventListener('change', scheduleAutosave);
  root.querySelectorAll('[data-review-action]').forEach(function (button) {
    button.addEventListener('click', function () {
      const action = button.dataset.reviewAction;
      if (action === 'save') saveLocal();
      if (action === 'export') exportReview();
      if (action === 'import' && importFile) importFile.click();
      if (action === 'print') window.print();
    });
  });
  if (importFile) {
    importFile.addEventListener('change', function () {
      const file = importFile.files && importFile.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = function () {
        try {
          if (applyReview(JSON.parse(reader.result))) saveLocal('Review imported and saved in this browser.');
        } catch (error) {
          setStatus('The selected review file is not valid JSON.');
        }
        importFile.value = '';
      };
      reader.readAsText(file);
    });
  }
  loadLocal();
}());
</script>'''


def stable_review_key(category: str, *parts: Any) -> str:
    """Return a stable, non-sensitive key for a review row across report updates."""
    material = "|".join(str(part or "") for part in (category, *parts))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"review-{digest}"


def render_html_report(config: dict[str, Any], plan: dict[str, Any]) -> str:
    """Render the baseline reviewer-oriented report for migration sizing.

    It uses the same plan/model, but prioritizes volumes, named human work,
    blockers, and planning drivers. It does not invent hours; the estimate
    columns are deliberately left for the reviewer to size.
    """
    model = build_report_model(config, plan)
    actions = model["actions"]
    skipped = model["skipped"]
    entities = model["entities"]
    source_summary = model["source_summary"]
    target_summary = model["target_summary"]
    target_diffed = model["target_diffed"]
    findings = model["findings"]
    automatic = model["automatic"]
    hybrid = model["hybrid"]
    prereq = model["prereq"]
    manual_only = model["manual_only"]
    blockers = model["blockers"]
    todos = model["todos"]
    verdict = model["verdict"]
    scope_exclusions = model["scope_exclusions"]
    disp = model["disp"]

    def esc(value: Any) -> str:
        return xml_escape(str(value)) if value not in (None, "") else ""

    def integer(value: Any) -> str:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return esc(value)

    def pct(numerator: int, denominator: int) -> str:
        return f"{(numerator / denominator * 100):.1f}%" if denominator else "0.0%"

    def badge(label: str, kind: str) -> str:
        return f'<span class="badge {kind}">{esc(label)}</span>'

    def disposition_badge(action: dict[str, Any]) -> str:
        label = disp(action)["disposition"]
        if label == DISPOSITION_AUTOMATIC:
            return badge("Automatic", "auto")
        if label == DISPOSITION_HYBRID:
            return badge("Hybrid", "hybrid")
        if label == DISPOSITION_PREREQUISITE:
            return badge("Manual prerequisite", "prereq")
        return badge("Manual only", "manual")

    def empty_row(columns: int, text: str) -> str:
        return f'<tr><td colspan="{columns}" class="empty">{esc(text)}</td></tr>'

    source_tenant = config.get("source", {}).get("tenant", "") or "(resolved from source token)"
    target_tenant = config.get("target", {}).get("tenant", "") or "(not provisioned)"
    source_url = config.get("source", {}).get("orchestrator_url", "")
    migration_mode = plan.get("migration_mode", config.get("migration_mode", ""))
    generated_at = plan.get("generated_at", "")
    source_total = model["source_records"]
    target_total = sum(int(target_summary.get(e, 0) or 0) for e in entities) if target_diffed else None
    human_actions = hybrid + prereq + manual_only
    binary_actions = [a for a in actions if a.get("entity") in {"packages", "libraries"}]
    target_text = integer(target_total) if target_total is not None else "n/a"
    review_scope = stable_review_key("report", source_tenant, source_url, target_tenant, migration_mode)
    overall_review_key = stable_review_key("overall", review_scope)

    def review_editor(category: str, *parts: Any) -> str:
        row_key = stable_review_key(category, *parts)
        field = lambda name: f"{row_key}:{name}"
        return (
            '<details class="review-details">'
            '<summary>Add / update review</summary>'
            '<div class="review-inline">'
            '<label>Status<select data-review-field="' + field("status") + '">'
            '<option value="Not reviewed">Not reviewed</option>'
            '<option value="Open">Open</option>'
            '<option value="In progress">In progress</option>'
            '<option value="Blocked">Blocked</option>'
            '<option value="Complete">Complete</option>'
            '<option value="Accepted">Accepted</option>'
            '</select></label>'
            '<label>Owner / reviewer<input data-review-field="' + field("owner") + '" type="text" placeholder="Name or team"></label>'
            '</div>'
            '<div class="review-inline">'
            '<label>Due date<input data-review-field="' + field("due_date") + '" type="date"></label>'
            '<label>Estimate (hours)<input data-review-field="' + field("estimate_hours") + '" type="number" min="0" step="0.5" placeholder="Optional"></label>'
            '</div>'
            '<div class="review-comment"><label>Review details / comments<textarea data-review-field="' + field("comment") + '" placeholder="Record decision, evidence, dependency, or follow-up"></textarea></label></div>'
            '</details>'
        )

    entity_rows: list[str] = []
    for entity in entities:
        entity_actions = [a for a in actions if a.get("entity") == entity]
        auto_n = sum(1 for a in entity_actions if disp(a)["disposition"] == DISPOSITION_AUTOMATIC)
        hybrid_n = sum(1 for a in entity_actions if disp(a)["disposition"] == DISPOSITION_HYBRID)
        prereq_n = sum(1 for a in entity_actions if disp(a)["disposition"] == DISPOSITION_PREREQUISITE)
        manual_n = sum(1 for a in entity_actions if disp(a)["disposition"] == DISPOSITION_MANUAL)
        skipped_n = sum(1 for item in skipped if item.get("entity") == entity)
        profile = entity_profile(entity)
        human_n = hybrid_n + prereq_n + manual_n
        if prereq_n:
            verdict_text, verdict_kind = "Human setup before apply", "prereq"
        elif manual_n and (auto_n or hybrid_n):
            verdict_text, verdict_kind = "Exclude mixed family", "manual"
        elif manual_n:
            verdict_text, verdict_kind = "Hand-build", "manual"
        elif hybrid_n:
            verdict_text, verdict_kind = "Automatic + follow-up", "hybrid"
        elif entity_actions:
            verdict_text, verdict_kind = "Fully automatic", "auto"
        else:
            verdict_text, verdict_kind = "No new work", "skip"
        entity_rows.append(
            "<tr>"
            f"<td><strong>{esc(entity)}</strong><br><span class=\"small\">{esc(profile['scope'])}</span></td>"
            f"<td class=\"num\">{integer(source_summary.get(entity, 0))}</td>"
            f"<td class=\"num\">{integer(target_summary.get(entity, 0)) if target_diffed else 'n/a'}</td>"
            f"<td class=\"num\">{integer(len(entity_actions))}</td>"
            f"<td class=\"num oktext\">{integer(auto_n)}</td>"
            f"<td class=\"num humantext\">{integer(hybrid_n)}</td>"
            f"<td class=\"num\">{integer(prereq_n)}</td>"
            f"<td class=\"num blocktext\">{integer(manual_n)}</td>"
            f"<td class=\"num\">{integer(skipped_n)}</td>"
            f"<td>{badge(verdict_text, verdict_kind)}</td>"
            f"<td class=\"estimate\">Reviewer to estimate</td>"
            f"<td class=\"review-cell\">{review_editor('entity', entity)}</td></tr>"
        )

    human_rows: list[str] = []
    for action in human_actions:
        detail = disp(action)
        human_rows.append(
            "<tr>"
            f"<td>{disposition_badge(action)}</td>"
            f"<td><strong>{esc(action.get('entity', ''))}</strong></td>"
            f"<td>{esc(action.get('identity', ''))}<br><span class=\"small\">{esc(record_folder(action.get('source_record', {})) or 'Tenant scope')}</span></td>"
            f"<td>{esc(detail['why'])}</td>"
            f"<td>{esc(detail['who_does_what'])}</td>"
            f"<td>{esc(detail['when'])}</td>"
            f"<td class=\"estimate\">Owner / hours</td>"
            f"<td class=\"review-cell\">{review_editor('human', action.get('entity', ''), action.get('identity', ''))}</td></tr>"
        )

    blocker_rows: list[str] = []
    for item in blockers + todos:
        kind = "blocker" if item["severity"] == "Blocker" else "prereq"
        blocker_rows.append(
            "<tr>"
            f"<td>{badge(item['severity'], kind)}</td>"
            f"<td>{esc(item['area'])}</td>"
            f"<td><strong>{esc(item['finding'])}</strong><br><span class=\"small\">Impact: {esc(item['impact'])}</span></td>"
            f"<td>{esc(item['resolution'])}</td>"
            f"<td class=\"estimate\">Owner / due date</td>"
            f"<td class=\"review-cell\">{review_editor('finding', item.get('area', ''), item.get('finding', ''))}</td></tr>"
        )

    planning_rows: list[str] = []
    for entity in entities:
        profile = entity_profile(entity)
        entity_actions = [a for a in actions if a.get("entity") == entity]
        human_n = sum(1 for a in entity_actions if disp(a)["disposition"] != DISPOSITION_AUTOMATIC)
        planning_rows.append(
            "<tr>"
            f"<td><strong>{esc(entity)}</strong></td>"
            f"<td>{esc(profile['migrates'])}</td>"
            f"<td>{esc(profile['retained'])}</td>"
            f"<td>{esc(profile['prerequisite'] or 'None identified')}</td>"
            f"<td>{esc(profile['post_action'] or 'None identified')}</td>"
            f"<td class=\"risk\">{esc(profile['risk'])}</td>"
            f"<td class=\"num\">{integer(human_n)}</td>"
            f"<td class=\"estimate\">Reviewer to estimate</td>"
            f"<td class=\"review-cell\">{review_editor('planning', entity)}</td></tr>"
        )

    skipped_rows = []
    for item in skipped:
        skipped_rows.append(
            f"<tr><td>{esc(item.get('entity', ''))}</td><td>{esc(item.get('identity', ''))}</td><td>{esc(item.get('reason', ''))}</td><td class=\"review-cell\">{review_editor('skipped', item.get('entity', ''), item.get('identity', ''))}</td></tr>"
        )
    excluded_items = not_migrated_items(entities, scope_exclusions)
    excluded_rows = "".join(
        f"<tr><td>{esc(item['area'])}</td><td>{esc(item['retained'])}</td><td>{esc(item['reason'])}</td><td class=\"review-cell\">{review_editor('excluded', item.get('area', ''))}</td></tr>"
        for item in excluded_items
    )
    sequence_items_parts: list[str] = []
    for item in apply_sequence_items(entities, actions):
        li_class = ' class="out"' if not item["in_scope"] else ""
        sequence_items_parts.append(
            f"<li{li_class}><span class=\"n\">{integer(item['position'])}</span><span><strong>{esc(item['entity'])}</strong> — {esc(item['planned'])} planned actions<br><span class=\"small\">Gate: {esc(item['gate'])}</span></span></li>"
        )
    sequence_items = "".join(sequence_items_parts)
    remediation_rows = "".join(
        f"<tr><td>{esc(item.get('entity', ''))}</td><td>{esc(item.get('identity', ''))}</td><td>{esc(item.get('required_action', ''))}</td><td class=\"estimate\">Owner / hours</td><td class=\"review-cell\">{review_editor('remediation', item.get('entity', ''), item.get('identity', ''))}</td></tr>"
        for item in plan.get("manual_remediation", [])
    )
    readiness_summary = (
        "No blockers identified in the current analysis."
        if not blockers and not todos
        else f"{integer(len(blockers))} blocker(s) and {integer(len(todos))} grouped action-required finding(s) need review; detailed human-work rows are listed separately."
    )

    return f'''<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Migration Effort Planning Report</title><style>{EFFORT_REPORT_CSS}</style></head>
<body><div class="wrap" data-review-scope="{review_scope}">
<header>
  <span class="eyebrow">UiPath Orchestrator Migration · Reviewer View</span>
  <h1>Migration Effort Planning Report</h1>
  <p class="meta">Source: <b>{esc(source_tenant)}</b> ({esc(source_url)}) · Target: <b>{esc(target_tenant)}</b> · Mode: <b>{esc(migration_mode)}</b> · Generated {esc(generated_at)}</p>
  <div class="verdict"><strong>{esc(verdict)}</strong><br><span class="small">This report is for sizing and planning. It does not authorize staging or target writes.</span></div>
  <div class="notice"><strong>How to use this report:</strong> review the decision-heavy rows, open <em>Add / update review</em> where needed, and record status, owner, due date, estimate, and comments. Review notes are local to this browser; use the Review workspace to save, export, or import them for later updates. No review entry changes the migration plan or target.</div>
</header>
<nav aria-label="Report sections">
  <a href="#effort-summary">Executive summary</a><a href="#effort-review">Review workspace</a><a href="#effort-readiness">Effort &amp; blockers</a><a href="#effort-entities">Entity inventory</a><a href="#effort-human-work">Human work details</a><a href="#effort-plan">Migration plan</a>
</nav>
<section class="panel" id="effort-review">
  <h2>Review workspace</h2>
  <p class="subnote">Use this workspace for the review record, then add row-level comments where a decision, owner, dependency, or follow-up needs more detail. Notes are kept in this browser and can be exported as JSON for a later report update.</p>
  <div class="review-workspace">
    <div class="review-toolbar">
      <button type="button" data-review-action="save">Save locally</button>
      <button type="button" data-review-action="export">Export review JSON</button>
      <button type="button" data-review-action="import">Import review JSON</button>
      <button type="button" data-review-action="print">Print review</button>
      <input id="review-import-file" type="file" accept="application/json,.json" hidden>
      <span id="review-save-state" class="review-state" aria-live="polite">No saved review for this report yet.</span>
    </div>
    <div class="review-grid">
      <div class="review-field"><label>Overall status<select data-review-field="{overall_review_key}:status"><option value="Not reviewed">Not reviewed</option><option value="Open">Open</option><option value="In progress">In progress</option><option value="Blocked">Blocked</option><option value="Complete">Complete</option><option value="Accepted">Accepted</option></select></label></div>
      <div class="review-field"><label>Review owner / reviewer<input data-review-field="{overall_review_key}:owner" type="text" placeholder="Name or team"></label></div>
      <div class="review-field"><label>Review date<input data-review-field="{overall_review_key}:date" type="date"></label></div>
      <div class="review-field"><label>Next review date<input data-review-field="{overall_review_key}:next_date" type="date"></label></div>
    </div>
    <div class="review-field review-summary"><label>Overall review details / comments<textarea data-review-field="{overall_review_key}:comment" placeholder="Record approval conditions, decisions, open risks, evidence, or update notes"></textarea></label></div>
    <p class="review-hint">The review layer is informational only. It does not edit the migration plan, change scope, call UiPath, or authorize target writes. Export the JSON before replacing this HTML with a later update.</p>
  </div>
</section>
<section class="panel" id="effort-summary">
  <h2>Executive summary</h2><p class="subnote">The scale of the migration and the amount of work that is automatic versus human-owned.</p>
  <div class="metrics">
    <div class="metric"><span class="value">{integer(len(entities))}</span><span class="label">Entity families in scope</span></div>
    <div class="metric"><span class="value">{integer(source_total)}</span><span class="label">Source records present</span></div>
    <div class="metric"><span class="value">{target_text}</span><span class="label">Target records present</span></div>
    <div class="metric"><span class="value">{integer(len(actions))}</span><span class="label">New planned actions</span></div>
    <div class="metric ok"><span class="value">{integer(len(automatic))}</span><span class="label">Fully automatic</span></div>
    <div class="metric human"><span class="value">{integer(len(human_actions))}</span><span class="label">Human-work actions</span></div>
    <div class="metric human"><span class="value">{pct(len(automatic), len(actions))}</span><span class="label">No-human-intervention coverage</span></div>
    <div class="metric"><span class="value">{integer(len(skipped))}</span><span class="label">Skipped / already present</span></div>
    <div class="metric warn"><span class="value">{integer(len(binary_actions))}</span><span class="label">Package/library binaries to stage</span></div>
    <div class="metric stop"><span class="value">{integer(len(blockers))}</span><span class="label">Blockers</span></div>
  </div>
  <div class="grid2">
    <div class="card"><h3>What this means for planning</h3><p><strong>{integer(len(automatic))}</strong> actions are tool-driven. <strong>{integer(len(human_actions))}</strong> actions need a human step, including <strong>{integer(len(hybrid))}</strong> hybrid actions that the tool applies first and then hands off.</p><p class="small">A skipped record is not new migration effort for this run, but it should be reconciled against the customer's expected scope.</p></div>
    <div class="card"><h3>Data and runtime boundaries</h3><p>Counts represent definitions and eligible records discovered at the snapshot time. Real passwords, provider secrets, machine keys, job history, audit history, and non-New queue items are not migrated.</p><p class="small">Use the entity inventory and migration-plan sections below to size specialist effort and cutover work.</p></div>
  </div>
</section>
<section class="panel" id="effort-readiness">
  <h2>Effort &amp; blockers</h2><p class="subnote">Go/no-go items first, followed by the operational effort drivers. {esc(readiness_summary)}</p>
  <div class="table-wrap"><table><thead><tr><th>Severity</th><th>Area</th><th>Finding and impact</th><th>Resolution / customer action</th><th>Planning owner</th><th>Review</th></tr></thead><tbody>{''.join(blocker_rows) or empty_row(6, 'No blockers or action-required findings were identified.')}</tbody></table></div>
  <div class="grid2"><div class="card"><h3>Disposition split</h3><p><span class="oktext"><strong>{integer(len(automatic))}</strong> automatic</span> · <span class="humantext"><strong>{integer(len(hybrid))}</strong> hybrid</span> · <strong>{integer(len(prereq))}</strong> prerequisite · <span class="blocktext"><strong>{integer(len(manual_only))}</strong> manual only</span></p><p class="small">Human-work totals are expanded in the next section; no category is represented only by an aggregate.</p></div><div class="card"><h3>Effort-estimation guidance</h3><p>Estimate each human-work row using its identity, reason, action, timing, and owner. Add contingency for credentials, identity reconciliation, provider configuration, trigger review, and cutover testing.</p></div></div>
</section>
<section class="panel" id="effort-entities">
  <h2>Entity inventory and disposition</h2><p class="subnote">Every entity family is reconciled across source, target, planned, automatic, human, and skipped volumes. “Reviewer to estimate” is intentionally blank work-sizing space.</p>
  <div class="table-wrap"><table><thead><tr><th>Entity family / scope</th><th>Source</th><th>Target</th><th>Planned</th><th>Automatic</th><th>Hybrid</th><th>Prereq</th><th>Manual only</th><th>Skipped</th><th>Family result</th><th>Estimate</th><th>Review</th></tr></thead><tbody>{''.join(entity_rows)}</tbody></table></div>
</section>
<section class="panel" id="effort-human-work">
  <h2>Human work details</h2><p class="subnote">This is the detailed register behind the aggregate human-work count: {integer(len(human_actions))} named actions across hybrid, prerequisite, and manual-only dispositions.</p>
  <div class="table-wrap"><table><thead><tr><th>Disposition</th><th>Entity</th><th>Identity / folder</th><th>Why human work is needed</th><th>Required action</th><th>Timing</th><th>Owner / hours</th><th>Review</th></tr></thead><tbody>{''.join(human_rows) or empty_row(8, 'No human-work actions were identified.')}</tbody></table></div>
  <h3 style="margin-top:24px">Post-migration remediation register</h3><p class="subnote">These are follow-up obligations that remain even after an automatic create succeeds.</p>
  <div class="table-wrap"><table><thead><tr><th>Entity</th><th>Identity</th><th>Required remediation</th><th>Owner / hours</th><th>Review</th></tr></thead><tbody>{remediation_rows or empty_row(5, 'No post-migration remediation items were recorded.')}</tbody></table></div>
</section>
<section class="panel" id="effort-plan">
  <h2>Migration plan and effort drivers</h2><p class="subnote">Use this section to plan sequencing, specialist ownership, and what remains outside automated migration.</p>
  <h3>Per-entity planning notes</h3><div class="table-wrap"><table><thead><tr><th>Entity</th><th>What migrates</th><th>What stays behind</th><th>Before apply</th><th>After apply</th><th>Principal risk</th><th>Human actions</th><th>Estimate</th><th>Review</th></tr></thead><tbody>{''.join(planning_rows)}</tbody></table></div>
  <h3 style="margin-top:24px">Dependency sequence</h3><ul class="sequence">{sequence_items}</ul>
  <h3 style="margin-top:24px">Skipped records</h3><p class="subnote">{integer(len(skipped))} records are not new actions in this plan. Review the reason before treating them as already covered.</p><div class="table-wrap"><table><thead><tr><th>Entity</th><th>Identity</th><th>Reason</th><th>Review</th></tr></thead><tbody>{''.join(skipped_rows) or empty_row(4, 'No records were skipped.')}</tbody></table></div>
  <h3 style="margin-top:24px">Not migrated by design</h3><div class="table-wrap"><table><thead><tr><th>Area</th><th>What stays behind</th><th>Reason</th><th>Review</th></tr></thead><tbody>{excluded_rows}</tbody></table></div>
</section>
<footer><p><strong>Read-only analysis.</strong> No binaries were downloaded and no target changes were made to produce this report.</p><p>This report contains tenant inventory. Keep it operator-local and sanitize before sharing.</p><p class="mono">Plan version {esc(plan.get('migration_plan_version', ''))} · Generated {esc(generated_at)}</p></footer>
{REVIEW_UI_SCRIPT}
</div></body></html>'''


def render_legacy_html_report(config: dict[str, Any], plan: dict[str, Any]) -> str:
    """Render the former fixed-template migration analysis report.

    Kept as an internal compatibility renderer; the public baseline report now
    uses the reviewer-oriented effort-planning template.
    """
    model = build_report_model(config, plan)
    actions, skipped, manual, entities = model["actions"], model["skipped"], model["manual"], model["entities"]
    source_summary, target_summary, findings = model["source_summary"], model["target_summary"], model["findings"]
    scope_exclusions = model["scope_exclusions"]
    disp, count = model["disp"], model["count"]
    blockers, todos = model["blockers"], model["todos"]
    automatic, hybrid, prereq, manual_only = model["automatic"], model["hybrid"], model["prereq"], model["manual_only"]
    assessing, verdict, target_diffed = model["assessing"], model["verdict"], model["target_diffed"]
    source_records = model["source_records"]
    planned_records = model["planned_records"]
    automation_coverage_percent = model["automation_coverage_percent"]

    def esc(value: Any) -> str:
        return xml_escape(str(value)) if value not in (None, "") else ""

    def has_value(value: Any) -> bool:
        return str(value).strip() not in ("", "-", "\u2014")

    generated_at = plan.get("generated_at", "")
    source_tenant = config.get("source", {}).get("tenant", "") or "(resolved from the token \u2014 tenant-scoped application)"
    source_url = config.get("source", {}).get("orchestrator_url", "")
    target_tenant = config.get("target", {}).get("tenant", "") or "(not provisioned)"
    migration_mode = plan.get("migration_mode", config.get("migration_mode", ""))

    if verdict.startswith("BLOCKED"):
        verdict_class = "v-blocked"
    elif verdict.startswith("ASSESSMENT"):
        verdict_class = "v-assess-blocked" if "would block" in verdict else "v-assess-clear"
    elif verdict.startswith("READY WITH"):
        verdict_class = "v-actions"
    else:
        verdict_class = "v-ready"

    def tile(label: str, value: Any, cls: str = "") -> str:
        return f'<div class="tile {cls}"><span class="tile-value">{esc(value)}</span><span class="tile-label">{esc(label)}</span></div>'

    tiles = "".join([
        tile("Entity families in scope", len(entities)),
        tile("Source records discovered", source_records),
        tile("Target records present", sum(int(target_summary.get(e, 0) or 0) for e in entities) if target_diffed else "n/a"),
        tile("Planned actions", planned_records),
        tile("Automatic", len(automatic), "t-auto"),
        tile("Automatic coverage", f"{automation_coverage_percent:.1f}%", "t-auto"),
        tile("Hybrid follow-up", len(hybrid), "t-hybrid"),
        tile("Manual prerequisite", len(prereq), "t-prereq"),
        tile("Manual only", len(manual_only), "t-manual"),
        tile("Skipped", len(skipped)),
        tile("Would block apply" if assessing else "Blockers", len(blockers), "t-blocker" if blockers else ""),
        tile("Actions required", len(todos)),
        tile("Remediation items", len(manual)),
    ])

    sev_class = {"Blocker": "sev-blocker", "Action required": "sev-action", "Information": "sev-info"}
    readiness_cards = "".join(
        f'''<article class="finding {sev_class.get(f["severity"], "sev-info")}" data-severity="{esc(f["severity"])}">
      <div class="finding-head"><span class="pill">{esc(f["severity"])}</span><span class="area">{esc(f["area"])}</span></div>
      <p class="finding-text">{esc(f["finding"])}</p>
      <p class="finding-impact"><strong>Impact if ignored:</strong> {esc(f["impact"])}</p>
      <p class="finding-resolution"><strong>Resolution:</strong> {esc(f["resolution"])}</p>
    </article>'''
        for f in findings
    )

    disp_rows = []
    for entity in entities:
        entity_actions = [a for a in actions if a.get("entity") == entity]
        auto_n = count(entity, DISPOSITION_AUTOMATIC)
        hybrid_n = count(entity, DISPOSITION_HYBRID)
        prereq_n = count(entity, DISPOSITION_PREREQUISITE)
        manual_n = count(entity, DISPOSITION_MANUAL)
        skipped_n = sum(1 for s in skipped if s.get("entity") == entity)
        src_n = int(source_summary.get(entity, 0) or 0)
        if not entity_actions:
            everdict = "Nothing to migrate"
        elif prereq_n:
            everdict = "Needs manual setup before apply"
        elif manual_n and (auto_n or hybrid_n):
            everdict = "Split \u2014 exclude from automated run"
        elif manual_n:
            everdict = "Hand-build only"
        elif hybrid_n:
            everdict = "Migrates, then follow-up"
        else:
            everdict = "Fully automatic"
        denom = max(src_n, 1)
        segs = [("t-auto", auto_n), ("t-hybrid", hybrid_n), ("t-prereq", prereq_n), ("t-manual", manual_n), ("t-skip", skipped_n)]
        bar = "".join(f'<span class="{cls}" style="width:{(n / denom * 100):.1f}%"></span>' for cls, n in segs if n)
        bar = bar or '<span class="t-empty" style="width:100%"></span>'
        caption = f"{auto_n} auto \u00b7 {hybrid_n} hybrid \u00b7 {prereq_n} prereq \u00b7 {manual_n} manual \u00b7 {skipped_n} skipped"
        disp_rows.append(f'''<tr>
      <td class="mono">{esc(entity)}</td>
      <td class="num">{src_n}</td>
      <td class="num">{esc(target_summary.get(entity, 0) if target_diffed else "n/a")}</td>
      <td class="num">{len(entity_actions)}</td>
      <td><div class="bar">{bar}</div><div class="bar-caption">{esc(caption)}</div></td>
      <td>{esc(everdict)}</td>
    </tr>''')
    disposition_table = "".join(disp_rows)

    def kv(label: str, value: Any) -> str:
        return f'<div class="kv"><span class="k">{esc(label)}</span><span class="v">{esc(value)}</span></div>' if has_value(value) else ""

    deep_cards = []
    for entity in entities:
        profile = entity_profile(entity)
        src_n = int(source_summary.get(entity, 0) or 0)
        planned_n = sum(1 for a in actions if a.get("entity") == entity)
        prereq_line = f'<p class="prereq"><strong>Before apply:</strong> {esc(profile["prerequisite"])}</p>' if has_value(profile["prerequisite"]) else ""
        post_line = f'<p class="postaction"><strong>After apply:</strong> {esc(profile["post_action"])}</p>' if has_value(profile["post_action"]) else ""
        deep_cards.append(f'''<article class="entity-card">
      <header><span class="entity-name mono">{esc(entity)}</span><span class="entity-counts">{src_n} source &middot; {planned_n} planned</span></header>
      <div class="entity-meta">{kv("Scope", profile["scope"])}{kv("Write path", profile["write_path"])}{kv("Depends on", profile["depends_on"])}</div>
      <p class="migrates"><strong>Migrates:</strong> {esc(profile["migrates"])}</p>
      <p class="retained"><strong>Stays behind:</strong> {esc(profile["retained"])}</p>
      {prereq_line}
      {post_line}
      <p class="risk"><strong>Risk:</strong> {esc(profile["risk"])}</p>
    </article>''')
    deep_dive = "".join(deep_cards)

    def checklist(items: list[dict[str, Any]], empty_text: str) -> str:
        if not items:
            return f'<p class="empty">{esc(empty_text)}</p>'
        rows = []
        for a in items:
            d = disp(a)
            rows.append(f'''<li class="check-item">
      <span class="box" aria-hidden="true"></span>
      <div>
        <div class="check-head"><span class="mono">{esc(a.get("identity", ""))}</span><span class="tag">{esc(a.get("entity", ""))}</span></div>
        <p class="why">{esc(d["why"])}</p>
        <p class="who"><strong>{esc(d["who_does_what"])}</strong> \u2014 {esc(d["when"])}</p>
      </div>
    </li>''')
        return f'<ul class="checklist">{"".join(rows)}</ul>'

    prereq_list = checklist(prereq, "Nothing in this scope needs manual setup before apply.")
    hybrid_list = checklist(hybrid, "Nothing in this scope needs a follow-up after apply.")
    manual_only_list = checklist(manual_only, "Nothing in this scope is hand-build only.")

    excluded_rows_html = "".join(
        f'<tr><td class="mono">{esc(i["area"])}</td><td>{esc(i["retained"])}</td><td>{esc(i["reason"])}</td></tr>'
        for i in not_migrated_items(entities, scope_exclusions)
    )

    def sequence_meta(item: dict[str, Any]) -> str:
        if not item["in_scope"]:
            return "out of scope — not an apply step"
        return f'{item["planned"]} planned{(" &middot; needs " + esc(item["gate"])) if item["gate"] else ""}'

    seq_html = "".join(
        f'''<li class="seq-item {"in" if i["in_scope"] else "out"}">
      <span class="seq-n">{i["position"]:02d}</span>
      <span class="seq-name mono">{esc(i["entity"])}</span>
      <span class="seq-meta">{sequence_meta(i)}</span>
    </li>'''
        for i in apply_sequence_items(entities, actions)
    )

    if manual:
        remediation_html = "".join(
            f'''<li class="check-item">
      <span class="box" aria-hidden="true"></span>
      <div>
        <div class="check-head"><span class="mono">{esc(i.get("identity", ""))}</span><span class="tag">{esc(i.get("entity", ""))}</span></div>
        <p class="why">{esc(i.get("reason", ""))}</p>
        <p class="who"><strong>{esc(i.get("required_action", ""))}</strong></p>
      </div>
    </li>'''
            for i in manual
        )
        remediation_html = f'<ul class="checklist">{remediation_html}</ul>'
    else:
        remediation_html = '<p class="empty">No post-migration remediation items in this scope.</p>'

    runbook_html = "".join(
        f'''<li class="runbook-item">
      <span class="rb-n">{esc(i["step"])}</span>
      <div><p class="rb-action">{esc(i["action"])}</p><p class="rb-purpose">{esc(i["purpose"])}</p></div>
    </li>'''
        for i in approval_runbook_items()
    )

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Migration Analysis Report</title>
<style>{REPORT_CSS}</style>
</head>
<body>
<div class="wrap">

  <header class="report-header">
    <span class="eyebrow">UiPath Orchestrator Migration</span>
    <h1>Migration Analysis Report</h1>
    <p class="meta-line">
      Source: <b>{esc(source_tenant)}</b> ({esc(source_url)}) &middot;
      Target: <b>{esc(target_tenant)}</b> &middot;
      Mode: <b>{esc(migration_mode)}</b> &middot;
      Generated {esc(generated_at)}
    </p>
    <div class="verdict {verdict_class}">
      <span class="dot"></span>
      <span class="verdict-text">{esc(verdict)}</span>
      <span class="printbtn">Print / Save as PDF from the browser menu</span>
    </div>
  </header>

  <div class="tabs">
    <nav class="tab-list" role="tablist" aria-label="Migration analysis views">
      <a class="tab-button" id="tab-overview-button" href="#tab-overview" role="tab" aria-controls="tab-overview">Overview</a>
      <a class="tab-button" id="tab-readiness-button" href="#tab-readiness" role="tab" aria-controls="tab-readiness">Readiness &amp; blockers</a>
      <a class="tab-button" id="tab-analysis-button" href="#tab-analysis" role="tab" aria-controls="tab-analysis">Migration analysis</a>
      <a class="tab-button" id="tab-followups-button" href="#tab-followups" role="tab" aria-controls="tab-followups">Follow-ups</a>
      <a class="tab-button" id="tab-plan-button" href="#tab-plan" role="tab" aria-controls="tab-plan">Plan &amp; approval</a>
    </nav>

    <main>
      <div class="tab-panel active" id="tab-overview" role="tabpanel" aria-labelledby="tab-overview-button">
        <section id="glance">
          <h2>At a glance</h2>
          <p class="section-note">Every number here also appears, row by row, in the companion Excel workbook \u2014 this is the same analysis, read top to bottom.</p>
          <div class="tiles">{tiles}</div>
        </section>
      </div>

      <div class="tab-panel" id="tab-readiness" role="tabpanel" aria-labelledby="tab-readiness-button">
        <section id="readiness">
          <h2>Readiness &amp; blockers</h2>
          <p class="section-note">Every <b>Blocker</b> must be cleared before apply can run. <b>Action required</b> items need an owner but do not block. <b>Information</b> items are context.</p>
          {readiness_cards}
        </section>
      </div>

      <div class="tab-panel" id="tab-analysis" role="tabpanel" aria-labelledby="tab-analysis-button">
        <section id="disposition">
          <h2>Disposition by entity</h2>
          <p class="section-note">The bar shows, out of each entity\'s source records: automatic, hybrid, manual-prerequisite, manual-only, and skipped.</p>
          <div class="table-wrap"><table>
            <thead><tr><th>Entity</th><th>Source</th><th>Target</th><th>Planned</th><th>Split</th><th>Verdict</th></tr></thead>
            <tbody>{disposition_table}</tbody>
          </table></div>
        </section>

        <section id="deepdive">
          <h2>Entity deep dive</h2>
          <p class="section-note">What migrates, what stays behind, and what a human must do \u2014 for every family in this scope.</p>
          {deep_dive}
        </section>
      </div>

      <div class="tab-panel" id="tab-followups" role="tabpanel" aria-labelledby="tab-followups-button">
        <section id="prereqs">
          <h2>Manual prerequisites</h2>
          <p class="section-note">Must exist in the target <b>before</b> apply runs, or apply fails.</p>
          {prereq_list}
        </section>

        <section id="hybrid">
          <h2>Hybrid follow-ups</h2>
          <p class="section-note">Applied automatically, then need a human step afterwards.</p>
          {hybrid_list}
        </section>

        <section id="manualonly">
          <h2>Manual only</h2>
          <p class="section-note">No create endpoint exists for these \u2014 they are hand-build work, not automation gaps.</p>
          {manual_only_list}
        </section>
      </div>

      <div class="tab-panel" id="tab-plan" role="tabpanel" aria-labelledby="tab-plan-button">
        <section id="excluded">
          <h2>Not migrated by design</h2>
          <p class="section-note">Confirm nothing here was expected to carry over.</p>
          <div class="table-wrap"><table>
            <thead><tr><th>Area</th><th>Not migrated</th><th>Reason</th></tr></thead>
            <tbody>{excluded_rows_html}</tbody>
          </table></div>
        </section>

        <section id="sequence">
          <h2>Apply sequence</h2>
          <p class="section-note">Dependency order, fixed. Dimmed entities are out of scope for this run.</p>
          <ul class="sequence">{seq_html}</ul>
        </section>

        <section id="remediation">
          <h2>Post-migration remediation</h2>
          <p class="section-note">Apply completing is not the migration completing \u2014 this list is the closing checklist.</p>
          {remediation_html}
        </section>

        <section id="runbook">
          <h2>Approval runbook</h2>
          <p class="section-note">The gates this run still has ahead of it.</p>
          <ul class="runbook">{runbook_html}</ul>
        </section>
      </div>
    </main>
  </div>

  <footer class="report-footer">
    <p><b>Read-only analysis.</b> No target changes have been made. Nothing was downloaded or applied to produce this report.</p>
    <p>This report and its companion workbook contain a full tenant inventory. Treat both as operator-local \u2014 do not commit or share without sanitizing.</p>
    <p class="mono">plan version {esc(plan.get("migration_plan_version", ""))} &middot; generated {esc(generated_at)}</p>
  </footer>

</div>

</body>
</html>'''


def write_html_report(config: dict[str, Any], plan: dict[str, Any], html_path: str | Path) -> None:
    Path(html_path).write_text(render_html_report(config, plan), encoding="utf-8")


def render_effort_html_report(config: dict[str, Any], plan: dict[str, Any]) -> str:
    """Compatibility alias for the promoted baseline reviewer report."""
    return render_html_report(config, plan)


def write_effort_html_report(config: dict[str, Any], plan: dict[str, Any], html_path: str | Path) -> None:
    Path(html_path).write_text(render_html_report(config, plan), encoding="utf-8")


def raw_value(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def nested_name(record: dict[str, Any], keys: list[str]) -> str:
    """Read a name out of either a flat field or an expanded nested object."""
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            nested = first_value(value, ["Name", "DisplayName", "FullyQualifiedName"])
            if nested:
                return nested
        elif value not in (None, ""):
            return str(value)
    return ""


def append_bool_flag(
    command: list[str],
    record: dict[str, Any],
    keys: list[str],
    true_flag: str,
    false_flag: str | None = None,
) -> None:
    value = raw_value(record, keys)
    if value is None:
        return
    if truthy(value):
        command.append(true_flag)
    elif false_flag:
        command.append(false_flag)


def json_argument(value: Any) -> str | None:
    """Render a value as a JSON string argument, passing through pre-encoded JSON."""
    if value in (None, "", {}, []):
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def process_input_arguments(record: dict[str, Any]) -> str | None:
    arguments = record.get("Arguments")
    if isinstance(arguments, dict):
        return json_argument(arguments.get("Input"))
    return json_argument(raw_value(record, ["InputArguments", "Arguments"]))


def tag_names(record: dict[str, Any]) -> str | None:
    tags = record.get("Tags")
    if isinstance(tags, list):
        names = [
            str(item.get("Name") if isinstance(item, dict) else item)
            for item in tags
            if item
        ]
        names = [name for name in names if name]
        if names:
            return ",".join(names)
        return None
    return first_value(record, ["Tags"]) or None


def trigger_kind(record: dict[str, Any]) -> str:
    """Classify a trigger. Explicit type wins; otherwise infer from the payload.

    Misclassifying a queue trigger as a time trigger creates a broken trigger
    in the target instead of failing, so inference errs toward the shape the
    record actually carries.
    """
    explicit = first_value(record, ["Type", "TriggerType"]).strip().lower()
    if explicit in {"queue", "queuetrigger"}:
        return "queue"
    if explicit in {"api", "apitrigger"}:
        return "api"
    if explicit in {"time", "timetrigger", "cron"}:
        return "time"
    if raw_value(record, ["QueueDefinitionId", "QueueDefinitionName", "QueueName", "QueueKey", "QueueDefinition"]):
        return "queue"
    if raw_value(record, ["Slug", "HttpMethod", "CallingMode"]):
        return "api"
    return "time"


NON_MIGRATABLE_SETTING_MARKERS = ("password", "apikey", "secret", "token", "connectionstring")
NON_MIGRATABLE_SETTING_KEYS = {
    "deploymenturl",
    "deployment.libraries.url",
    "deployment.activities.url",
    "nuget.packages.apikey",
    "nuget.activities.apikey",
}


DIRECTORY_PRINCIPAL_TYPES = {
    "user": "DirectoryUser",
    "directoryuser": "DirectoryUser",
    "group": "DirectoryGroup",
    "directorygroup": "DirectoryGroup",
    "robot": "DirectoryRobot",
    "directoryrobot": "DirectoryRobot",
    "externalapplication": "DirectoryExternalApplication",
    "directoryexternalapplication": "DirectoryExternalApplication",
    "application": "DirectoryExternalApplication",
}


def directory_principal_type(record: dict[str, Any]) -> str:
    """Map a source principal type onto the Directory* type the import command requires."""
    raw = first_value(record, ["Type", "PrincipalType", "UserType"]).strip().lower()
    return DIRECTORY_PRINCIPAL_TYPES.get(raw, "DirectoryUser")


def role_permission_names(record: dict[str, Any]) -> list[str]:
    """Permission names on a role, from either a flat list or an expanded collection."""
    permissions = record.get("Permissions") or record.get("RolePermissions") or []
    if isinstance(permissions, dict):
        permissions = permissions.get("value") or permissions.get("Data") or []
    names: list[str] = []
    for item in permissions if isinstance(permissions, list) else []:
        if isinstance(item, dict):
            candidate = first_value(item, ["Name", "PermissionName", "DisplayName"])
        else:
            candidate = str(item)
        if candidate:
            names.append(candidate)
    return names


def assigned_folder_role_names(record: dict[str, Any]) -> list[str]:
    """Folder-scoped role names assigned to a principal, when discovery expanded them."""
    assignments = record.get("RolesList") or record.get("Roles") or []
    if isinstance(assignments, str):
        return [item.strip() for item in assignments.split(",") if item.strip()]
    names: list[str] = []
    for item in assignments if isinstance(assignments, list) else []:
        if isinstance(item, dict):
            candidate = first_value(item, ["Name", "RoleName", "DisplayName"])
        else:
            candidate = str(item)
        if candidate:
            names.append(candidate)
    return names


def calendar_excluded_dates(record: dict[str, Any]) -> list[str]:
    dates = record.get("ExcludedDates") or record.get("excludedDates") or []
    if isinstance(dates, list):
        return [str(item) for item in dates if item]
    return []


def setting_is_migratable(record: dict[str, Any]) -> bool:
    """Tenant settings that carry secrets or point at the source deployment are not copied."""
    key = first_value(record, ["Name", "Key", "SettingName"]).strip()
    if not key:
        return False
    lowered = key.lower()
    if lowered in NON_MIGRATABLE_SETTING_KEYS:
        return False
    return not any(marker in lowered.replace("_", "") for marker in NON_MIGRATABLE_SETTING_MARKERS)


# ---------------------------------------------------------------------------
# Target key resolution
#
# Several target create commands need GUIDs that exist only in the target
# tenant: a trigger's release/queue/calendar key, an asset's credential-store
# key, a role's key for the permission grant. Those are looked up by name
# against the target after each entity family is applied.
# ---------------------------------------------------------------------------

RESOLVER_LIST_COMMANDS = {
    "folders": ["uip", "or", "folders", "list", "--all", "--output", "json"],
    "roles": ["uip", "or", "roles", "list", "--output", "json"],
    "queues": ["uip", "or", "queues", "list", "--output", "json"],
    "calendars": ["uip", "or", "calendars", "list", "--output", "json"],
    "credential_stores": ["uip", "or", "credential-stores", "list", "--output", "json"],
    "processes": ["uip", "or", "processes", "list", "--output", "json"],
    "buckets": ["uip", "or", "buckets", "list", "--output", "json"],
}

FOLDER_SCOPED_RESOLVERS = {"queues", "processes", "buckets"}

# OData paths address these by numeric Id, not by GUID key.
ID_PREFERRING_FAMILIES = {"buckets"}

# Entity family -> resolver family whose cache it invalidates once applied.
RESOLVER_FAMILY_BY_ENTITY = {
    "folders": "folders",
    "roles": "roles",
    "queues": "queues",
    "calendars": "calendars",
    "processes": "processes",
    "credential_stores": "credential_stores",
}

KEY_FIELDS = ["Key", "key", "Id", "id"]


class TargetResolver:
    """Resolves target-tenant keys by name, caching one lookup per family/folder.

    `offline=True` returns readable placeholders instead of querying, so a plan
    can be rendered for review without a target connection. Apply always runs
    with a live resolver and rejects placeholders.
    """

    PLACEHOLDER_PREFIX = "<unresolved:"

    def __init__(self, config: dict[str, Any], offline: bool = False) -> None:
        self.config = config
        self.target_config = config.get("target", {}) or {}
        self.offline = offline
        self._cache: dict[str, dict[str, str]] = {}

    def invalidate(self, *families: str) -> None:
        if not families:
            self._cache.clear()
            return
        for family in families:
            for cache_key in [key for key in self._cache if key.startswith(f"{family}:")]:
                self._cache.pop(cache_key, None)

    @staticmethod
    def _record_names(family: str, record: dict[str, Any]) -> list[str]:
        if family == "folders":
            return [folder_path(record), first_value(record, ["Name"])]
        if family == "processes":
            return [
                first_value(record, ["Name"]),
                first_value(record, ["ProcessKey"]),
                first_value(record, ["Key"]),
            ]
        return [first_value(record, ["Name", "DisplayName"])]

    def _lookup_table(self, family: str, folder: str | None = None) -> dict[str, str]:
        cache_key = f"{family}:{folder or ''}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        command = list(RESOLVER_LIST_COMMANDS[family])
        if folder and family in FOLDER_SCOPED_RESOLVERS:
            command.extend(["--folder-path", folder])
        command.extend(str(arg) for arg in self.target_config.get("uip_extra_args", []))
        try:
            records = run_paginated_command(command, self.target_config)
        except SystemExit:
            records = []
        fields = ["Id", "id", *KEY_FIELDS] if family in ID_PREFERRING_FAMILIES else KEY_FIELDS
        table: dict[str, str] = {}
        for record in records:
            key = first_value(record, fields)
            if not key:
                continue
            for name in self._record_names(family, record):
                if name:
                    table.setdefault(name, key)
        self._cache[cache_key] = table
        return table

    def target_folder_id(self, path: str) -> Any | None:
        """Numeric folder Id for the X-UIPATH-OrganizationUnitId header."""
        if not path:
            return None
        if self.offline:
            return f"{self.PLACEHOLDER_PREFIX}folder-id:{path}>"
        cache_key = "folder-ids:"
        if cache_key not in self._cache:
            command = list(RESOLVER_LIST_COMMANDS["folders"])
            command.extend(str(arg) for arg in self.target_config.get("uip_extra_args", []))
            try:
                records = run_paginated_command(command, self.target_config)
            except SystemExit:
                records = []
            table = {}
            for record in records:
                identifier = first_value(record, ["Id", "id"])
                for name in (folder_path(record), first_value(record, ["Name"])):
                    if name and identifier:
                        table.setdefault(name, identifier)
            self._cache[cache_key] = table
        return self._cache[cache_key].get(path)

    def resolve(self, family: str, name: str, folder: str | None = None) -> str | None:
        if not name:
            return None
        if self.offline:
            return f"{self.PLACEHOLDER_PREFIX}{family}:{name}>"
        return self._lookup_table(family, folder).get(name)

    def require(self, family: str, name: str, what: str, folder: str | None = None) -> str:
        key = self.resolve(family, name, folder)
        if not key:
            scope = f" in folder '{folder}'" if folder else ""
            fail(
                f"Cannot resolve target {what} '{name}'{scope}. "
                f"Apply the {family} it depends on first, then re-run apply."
            )
        return str(key)


def credential_store_key_for(
    record: dict[str, Any], config: dict[str, Any], resolver: TargetResolver | None
) -> str | None:
    """Target credential-store key for a credential asset or external bucket.

    Credential stores are created through the Cloud API as definitions only; provider
    secrets and protected configuration never travel. The target key for credential
    assets comes from an operator-supplied mapping or a single configured default.
    Source store IDs are meaningless in the target and are never reused.
    """
    store_name = nested_name(record, ["CredentialStore", "CredentialStoreName"])
    mapping = config.get("credential_store_key_map") or {}
    if store_name and store_name in mapping:
        return str(mapping[store_name])
    source_id = first_value(record, ["CredentialStoreId", "CredentialStoreKey"])
    if source_id and source_id in mapping:
        return str(mapping[source_id])
    if store_name and resolver:
        resolved = resolver.resolve("credential_stores", store_name)
        if resolved:
            return str(resolved)
    default_key = config.get("target_credential_store_key")
    return str(default_key) if default_key else None


def action_command(
    action: dict[str, Any],
    config: dict[str, Any],
    resolver: TargetResolver | None = None,
) -> list[list[str]]:
    record = action["source_record"]
    entity = action["entity"]
    folder = record_folder(record)
    name = record_name(record)
    if resolver is None:
        resolver = TargetResolver(config, offline=True)
    if entity == "folders":
        folder_name, parent = folder_create_name_parent(record)
        command = ["uip", "or", "folders", "create", folder_name or identity(entity, record), "--output", "json"]
        if parent:
            command.extend(["--parent", parent])
        append_if_value(command, "--description", first_value(record, ["Description"]))
        append_if_value(command, "--feed-type", first_value(record, ["FeedType", "PackageFeedType"]))
        append_if_value(command, "--permission-model", first_value(record, ["PermissionModel"]))
        append_if_value(command, "--provision-type", first_value(record, ["ProvisionType"]))
        return [command]
    if entity == "roles":
        role_type = first_value(record, ["Type", "RoleType"], "Folder")
        return [["uip", "or", "roles", "create", "--name", name, "--type", role_type, "--output", "json"]]
    if entity == "users":
        username = first_value(record, ["UserName", "Username", "EmailAddress", "Email"])
        directory_id = first_value(record, ["DirectoryIdentifier", "DirectoryId"])
        command = ["uip", "or", "users", "import", "--type", directory_principal_type(record), "--output", "json"]
        if directory_id:
            command.extend(["--directory-id", directory_id])
        else:
            command.extend(["--username", username])
        append_if_value(command, "--domain", first_value(record, ["Domain"]))
        role_names = assigned_folder_role_names(record)
        if role_names and folder:
            role_keys = [resolver.resolve("roles", role_name) for role_name in role_names]
            role_keys = [key for key in role_keys if key]
            if role_keys:
                command.extend(["--folder-path", folder, "--role-keys", ",".join(str(k) for k in role_keys)])
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
        if asset_type.lower() == "bool":
            # The API rejects Python-style True/False casing.
            value = "true" if truthy(value) else "false"
        command = ["uip", "or", "assets", "create", name, value, "--type", asset_type, "--output", "json"]
        if folder:
            command.extend(["--folder-path", folder])
        append_if_value(command, "--scope", first_value(record, ["ValueScope", "Scope"]))
        append_if_value(command, "--description", first_value(record, ["Description"]))
        append_if_value(command, "--tags", tag_names(record))
        if asset_type.lower() in {"credential", "secret"}:
            store_key = credential_store_key_for(record, config, resolver)
            if not store_key:
                fail(
                    f"Credential asset '{name}' needs a target credential store. "
                    "Set config.target_credential_store_key (or credential_store_key_map) "
                    "to a store key that exists in the target tenant."
                )
            command.extend(["--credential-store-key", str(store_key)])
        return [command]
    if entity == "queues":
        command = ["uip", "or", "queues", "create", name, "--output", "json"]
        if folder:
            command.extend(["--folder-path", folder])
        append_if_value(command, "--description", first_value(record, ["Description"]))
        append_if_value(command, "--max-retries", first_value(record, ["MaxNumberOfRetries", "MaxRetries"]))
        append_if_value(command, "--sla-in-minutes", first_value(record, ["SlaInMinutes"]))
        append_if_value(command, "--risk-sla-in-minutes", first_value(record, ["RiskSlaInMinutes"]))
        append_bool_flag(command, record, ["AcceptAutomaticallyRetry", "AutoRetry"], "--auto-retry", "--no-auto-retry")
        append_bool_flag(command, record, ["RetryAbandonedItems"], "--retry-abandoned-items", "--no-retry-abandoned-items")
        append_bool_flag(
            command,
            record,
            ["EnforceUniqueReference"],
            "--enforce-unique-reference",
            "--no-enforce-unique-reference",
        )
        append_bool_flag(command, record, ["EncryptData", "IsEncrypted"], "--encrypted")
        append_if_value(command, "--retention-action", first_value(record, ["RetentionAction", "ProcessedItemsRetentionAction"]))
        append_if_value(command, "--retention-period", first_value(record, ["RetentionPeriod", "ProcessedItemsRetentionPeriod"]))
        append_if_value(command, "--stale-retention-action", first_value(record, ["StaleRetentionAction", "UnprocessedItemsRetentionAction"]))
        append_if_value(command, "--stale-retention-period", first_value(record, ["StaleRetentionPeriod", "UnprocessedItemsRetentionPeriod"]))
        return [command]
    if entity == "storage_buckets":
        command = ["uip", "or", "buckets", "create", name, "--output", "json"]
        if folder:
            command.extend(["--folder-path", folder])
        append_if_value(command, "--description", first_value(record, ["Description"]))
        append_if_value(command, "--identifier", first_value(record, ["Identifier"]))
        append_if_value(command, "--storage-provider", first_value(record, ["StorageProvider"]))
        append_if_value(command, "--storage-parameters", first_value(record, ["StorageParameters"]))
        append_if_value(command, "--storage-container", first_value(record, ["StorageContainer"]))
        # Only external providers hold a secret in a credential store. Passing a
        # store key to an Orchestrator built-in bucket is rejected by the API.
        if first_value(record, ["StorageProvider"]):
            append_if_value(command, "--credential-store-key", credential_store_key_for(record, config, resolver))
        append_if_value(command, "--external-name", first_value(record, ["ExternalName"]))
        append_if_value(command, "--options", first_value(record, ["Options"]))
        append_if_value(command, "--tags", json_argument(record.get("Tags")))
        return [command]
    if entity == "packages":
        destination = str(package_file_path(config, record))
        return [["uip", "or", "packages", "upload", destination, "--output", "json"]]
    if entity == "libraries":
        destination = str(library_file_path(config, record))
        command = ["uip", "or", "libraries", "upload", "--file", destination, "--output", "json"]
        target_config = config.get("target", {})
        append_if_value(command, "--feed-id", target_config.get("library_feed_id") or config.get("library_feed_id"))
        return [command]
    if entity == "processes":
        package_key = first_value(record, ["PackageId", "PackageKey", "ProcessKey"])
        version = first_value(record, ["PackageVersion", "ProcessVersion", "Version"])
        command = ["uip", "or", "processes", "create", "--name", name, "--package-key", package_key, "--output", "json"]
        if version:
            command.extend(["--package-version", version])
        if folder:
            command.extend(["--folder-path", folder])
        append_if_value(command, "--description", first_value(record, ["Description"]))
        append_if_value(command, "--entry-point", first_value(record, ["EntryPointPath", "EntryPoint"]))
        append_if_value(command, "--input-arguments", process_input_arguments(record))
        specific_priority = first_value(record, ["SpecificPriorityValue"])
        if specific_priority:
            command.extend(["--specific-priority", specific_priority])
        else:
            append_if_value(command, "--job-priority", first_value(record, ["JobPriority"]))
        append_if_value(command, "--robot-size", first_value(record, ["RobotSize"]))
        append_if_value(command, "--tags", tag_names(record))
        append_if_value(command, "--environment-variables", json_argument(record.get("EnvironmentVariables")))
        append_bool_flag(command, record, ["AutoUpdate"], "--auto-update", "--no-auto-update")
        append_bool_flag(
            command,
            record,
            ["HiddenForAttendedUser", "IsHiddenForAttended"],
            "--hidden-for-attended",
            "--visible-for-attended",
        )
        append_if_value(command, "--retention-action", first_value(record, ["JobRetentionAction", "RetentionAction"]))
        append_if_value(command, "--retention-period", first_value(record, ["JobRetentionPeriod", "RetentionPeriod"]))
        append_if_value(command, "--stale-retention-action", first_value(record, ["StaleJobRetentionAction", "StaleRetentionAction"]))
        append_if_value(command, "--stale-retention-period", first_value(record, ["StaleJobRetentionPeriod", "StaleRetentionPeriod"]))
        return [command]
    if entity == "calendars":
        command = ["uip", "or", "calendars", "create", name, "--output", "json"]
        append_if_value(command, "--time-zone", first_value(record, ["TimeZoneId", "TimeZone"], "UTC"))
        return [command]
    if entity == "triggers":
        # The trigger's folder is derived by the CLI from --release-key, so no
        # folder flag is passed here. Release/queue/calendar keys are target
        # GUIDs and must be resolved against the target tenant.
        kind = trigger_kind(record)
        release_name = nested_name(record, ["Release", "ReleaseName", "ProcessName"])
        release_key = resolver.require("processes", release_name, "process (release)", folder)
        command = [
            "uip", "or", "triggers", "create",
            "--type", kind,
            "--name", name,
            "--release-key", release_key,
            "--output", "json",
        ]
        append_if_value(command, "--description", first_value(record, ["Description"]))
        append_if_value(command, "--runtime-type", first_value(record, ["RuntimeType", "RobotType"], "Unattended"))
        append_if_value(command, "--job-priority", first_value(record, ["JobPriority", "Priority"]))
        append_if_value(command, "--input-arguments", json_argument(raw_value(record, ["InputArguments"])))

        calendar_name = nested_name(record, ["Calendar", "CalendarName"])
        if calendar_name:
            append_if_value(command, "--calendar-key", resolver.resolve("calendars", calendar_name))

        if kind == "time":
            append_if_value(command, "--cron", first_value(record, ["Cron", "CronExpression", "StartProcessCron"]))
            append_if_value(command, "--time-zone", first_value(record, ["TimeZoneId", "TimeZone"], "UTC"))
            append_if_value(command, "--stop-strategy", first_value(record, ["StopStrategy"]))
            append_if_value(command, "--kill-process-expression", first_value(record, ["KillProcessExpression", "StopProcessExpression"]))
        elif kind == "queue":
            queue_name = nested_name(record, ["QueueDefinition", "QueueDefinitionName", "QueueName"])
            command.extend(["--queue-key", resolver.require("queues", queue_name, "queue", folder)])
            append_if_value(command, "--items-threshold", first_value(record, ["ItemsActivationThreshold", "QueueItemsThreshold"]))
            append_if_value(command, "--max-jobs", first_value(record, ["MaxNumberOfJobs", "MaximumJobsCount", "MaxJobs"]))
            append_if_value(command, "--items-per-job", first_value(record, ["ItemsPerJob", "TargetRatioOfItemsPerJob"]))
            append_bool_flag(command, record, ["ActivateOnComplete", "ReactivateOnComplete"], "--activate-on-complete")
        elif kind == "api":
            append_if_value(command, "--slug", first_value(record, ["Slug"]))
            append_if_value(command, "--method", first_value(record, ["HttpMethod", "Method"]))
            append_if_value(command, "--calling-mode", first_value(record, ["CallingMode"]))

        append_bool_flag(command, record, ["ResumeOnSameContext"], "--resume-on-same-context")
        append_bool_flag(command, record, ["RunAsMe"], "--run-as-me")
        enabled = record.get("Enabled")
        if (enabled not in (None, "") and not truthy(enabled)) or truthy(record.get("Disabled")):
            command.append("--disabled")
        return [command]
    if entity == "settings":
        setting_key = first_value(record, ["Name", "Key", "SettingName"])
        setting_value = first_value(record, ["Value", "SettingValue"])
        return [["uip", "or", "settings", "update", setting_key, setting_value, "--output", "json"]]
    if entity == "webhooks":
        command = ["uip", "or", "webhooks", "create", name, "--url", first_value(record, ["Url", "URL", "EndpointUrl"]), "--output", "json"]
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


# Entities applied through the Cloud API instead of the uip CLI, because the CLI
# cannot express the fields a faithful migration needs.
REST_APPLIED_ENTITIES = {"calendars", "credential_stores", "queue_items", "bucket_files"}


def queue_item_payload(record: dict[str, Any], queue_name: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "Name": queue_name,
        "Priority": first_value(record, ["Priority"], "Normal"),
        "SpecificContent": record.get("SpecificContent") or {},
    }
    for field, keys in (
        ("Reference", ["Reference"]),
        ("DeferDate", ["DeferDate"]),
        ("DueDate", ["DueDate"]),
        ("RiskSlaDate", ["RiskSlaDate"]),
        ("Source", ["Source"]),
    ):
        value = raw_value(record, keys)
        if value is not None:
            item[field] = value
    return item


def queue_item_batch_key(action: dict[str, Any]) -> tuple[str, str]:
    """Queue items batch per (folder, queue) — the bulk endpoint takes one queue."""
    record = action["source_record"]
    return (
        record_folder(record),
        first_value(record, ["QueueDefinitionName", "QueueName"]),
    )


def rest_apply_queue_items(
    actions: list[dict[str, Any]],
    config: dict[str, Any],
    resolver: TargetResolver,
) -> list[str]:
    """Add a batch of New-state items to one target queue in a single call."""
    folder, queue_name = queue_item_batch_key(actions[0])
    if not queue_name:
        fail(f"Queue item {actions[0]['identity']} has no queue name; cannot target a queue.")
    folder_id = resolver.target_folder_id(folder) if folder else None
    payload = {
        "queueName": queue_name,
        "commitType": "ProcessAllIndependently",
        "queueItems": [queue_item_payload(a["source_record"], queue_name) for a in actions],
    }
    cloud_rest_request(
        config, "POST",
        "/odata/Queues/UiPathODataSvc.BulkAddQueueItems",
        payload, folder_id=folder_id,
    )
    return [f"POST BulkAddQueueItems -> {queue_name} ({len(actions)} item(s))"]


def rest_apply_action(
    action: dict[str, Any],
    config: dict[str, Any],
    resolver: TargetResolver,
) -> list[str]:
    """Apply one action through the Cloud API. Returns human-readable step labels."""
    record = action["source_record"]
    entity = action["entity"]
    folder = record_folder(record)
    folder_id = resolver.target_folder_id(folder) if folder else None
    steps: list[str] = []

    if entity == "calendars":
        payload = {
            "Name": record_name(record),
            "TimeZoneId": first_value(record, ["TimeZoneId", "TimeZone"], "UTC"),
            "ExcludedDates": calendar_excluded_dates(record),
        }
        cloud_rest_request(config, "POST", "/odata/Calendars", payload)
        steps.append(
            f"POST /odata/Calendars ({len(payload['ExcludedDates'])} excluded date(s))"
        )
        return steps

    if entity == "credential_stores":
        payload = {
            "Name": record_name(record),
            "Type": first_value(record, ["Type", "StoreType"]),
            "HostName": first_value(record, ["HostName"]),
            "AdditionalConfiguration": first_value(record, ["AdditionalConfiguration"]),
        }
        payload = {key: value for key, value in payload.items() if value not in (None, "")}
        cloud_rest_request(config, "POST", "/odata/CredentialStores", payload)
        steps.append("POST /odata/CredentialStores")
        return steps

    if entity == "queue_items":
        return rest_apply_queue_items([action], config, resolver)

    if entity == "bucket_files":
        bucket_name = first_value(record, ["BucketName"])
        file_path_value = first_value(record, ["Name"])
        source_config = config.get("source", {})
        source_bucket_id = first_value(record, ["BucketId"])
        source_folder_id = record.get("OrganizationUnitId")
        quoted = urllib.parse.quote(file_path_value, safe="")

        read_payload = direct_rest_get(
            source_config,
            f"/odata/Buckets({source_bucket_id})/UiPath.Server.Configuration.OData.GetReadUri?path={quoted}",
            source_folder_id,
        )
        read_uri, read_headers = presigned_uri_and_headers(read_payload)
        if not read_uri:
            fail(f"Source did not return a read URI for {action['identity']}.")
        data = presigned_download(read_uri, read_headers)
        steps.append(f"GET source read URI ({len(data)} bytes)")

        target_bucket_id = resolver.require("buckets", bucket_name, "storage bucket", folder)
        write_payload = cloud_rest_request(
            config, "GET",
            f"/odata/Buckets({target_bucket_id})/UiPath.Server.Configuration.OData.GetWriteUri?path={quoted}",
            folder_id=folder_id,
        )
        write_uri, write_headers = presigned_uri_and_headers(write_payload)
        if not write_uri:
            fail(f"Target did not return a write URI for {action['identity']}.")
        presigned_upload(write_uri, data, write_headers)
        steps.append(f"PUT target write URI -> {bucket_name}/{file_path_value}")
        return steps

    fail(f"No REST apply implemented for {entity}")
    return steps


def action_has_post_step(action: dict[str, Any]) -> bool:
    """Whether an action needs a follow-up command after its create succeeds."""
    if action.get("operation") != "create":
        return False
    if action.get("entity") == "roles":
        return bool(role_permission_names(action["source_record"]))
    return False


def action_post_commands(
    action: dict[str, Any],
    config: dict[str, Any],
    resolver: TargetResolver,
) -> list[list[str]]:
    """Commands that can only be built after the create has run.

    `roles create` deliberately creates a role with no permissions; the grant is
    a separate update keyed by the new role's GUID, which does not exist until
    the create succeeds.
    """
    record = action["source_record"]
    entity = action["entity"]
    if entity != "roles" or action.get("operation") != "create":
        return []
    permissions = role_permission_names(record)
    if not permissions:
        return []
    role_key = resolver.resolve("roles", record_name(record))
    if not role_key:
        return []
    return [[
        "uip", "or", "roles", "update", str(role_key),
        "--add-permissions", ",".join(permissions),
        "--output", "json",
    ]]


def readiness_blocker_errors(config: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """Return every hard readiness blocker that must stop staging/apply."""
    readiness = plan.get("readiness")
    if not isinstance(readiness, list):
        return [
            "Plan is missing readiness findings; regenerate it with analyze before applying."
        ]
    checks = list(readiness) + readiness_findings(config, plan)
    errors: list[str] = []
    seen: set[str] = set()
    for finding in checks:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("blocks_apply", "")).strip().casefold() != "yes":
            continue
        text = str(finding.get("finding") or finding.get("area") or "Readiness blocker")
        if text not in seen:
            seen.add(text)
            errors.append(f"Readiness blocker: {text}")
    return errors


def validate_plan(config: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    require_target(config, "validate a plan")
    errors: list[str] = []
    stored_signature = plan.get("target_inventory_signature")
    if not stored_signature:
        errors.append(
            "Plan is missing the approved target inventory fingerprint. "
            "Regenerate it with analyze before validating."
        )
    else:
        # The target snapshot used for analysis is evidence, not authorization.
        # Re-discover the configured target now and compare natural keys before
        # any apply; this catches objects created or changed since approval.
        live_target = discover(config, "target")
        live_errors = live_target.get("errors") or []
        if live_errors:
            errors.append(
                "Live target discovery failed during immediate validation; "
                "the target cannot be proven unchanged. Regenerate discovery and retry."
            )
        else:
            entities = selected_entities(config)
            actual = inventory_keys(live_target, entities)
            baseline = plan.get("target_inventory_keys") or {}
            if inventory_keys_signature(baseline, entities) != stored_signature:
                errors.append(
                    "Plan target inventory metadata is inconsistent. "
                    "Regenerate the analysis plan before validating."
                )
            else:
                # A canary/resume may have created some planned objects, so the
                # live inventory must contain the approved baseline plus only a
                # subset of planned action identities.
                planned_counts: dict[str, Counter[str]] = {
                    entity: Counter() for entity in entities
                }
                for action in plan.get("actions", []):
                    entity = str(action.get("entity", ""))
                    if entity in planned_counts:
                        planned_counts[entity][str(action.get("identity", ""))] += 1
                changed: list[str] = []
                for entity in entities:
                    actual_counts = Counter(actual.get(entity, []))
                    baseline_counts = Counter(baseline.get(entity, []))
                    removed = any(
                        actual_counts[key] < count
                        for key, count in baseline_counts.items()
                    )
                    extras = actual_counts - baseline_counts
                    unexpected = any(
                        count > planned_counts[entity][key]
                        for key, count in extras.items()
                    )
                    if removed or unexpected:
                        changed.append(entity)
                if changed:
                    errors.append(
                        "Target drift detected since analysis for: "
                        + ", ".join(changed)
                        + ". Regenerate the analysis plan and revalidate immediately before apply."
                    )
    readiness = plan.get("readiness")
    if not isinstance(readiness, list):
        errors.append("Plan is missing readiness findings; regenerate it with analyze before applying.")
    readiness_checks = list(readiness or []) + readiness_findings(config, plan)
    readiness_seen: set[str] = set()
    for finding in readiness_checks:
        if not isinstance(finding, dict) or str(finding.get("blocks_apply", "")).strip().casefold() != "yes":
            continue
        text = str(finding.get("finding") or finding.get("area") or "Readiness blocker")
        if text not in readiness_seen:
            readiness_seen.add(text)
            errors.append(f"Readiness blocker: {text}")
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
            if not (
                config.get("target_credential_store_key")
                or config.get("credential_store_key_map")
                or nested_name(action.get("source_record", {}), ["CredentialStore", "CredentialStoreName"])
            ):
                errors.append(
                    f"Credential asset {action.get('identity')} has no resolvable target credential store. "
                    "Set config.target_credential_store_key to a store key in the target tenant."
                )
        if entity in ("bucket_files", "queue_items"):
            parent = "storage_buckets" if entity == "bucket_files" else "queues"
            in_scope = parent in selected
            present_in_target = int(plan.get("target_summary", {}).get(parent, 0) or 0) > 0
            if not in_scope and not present_in_target:
                errors.append(
                    f"'{entity}' is in scope but '{parent}' is neither in scope nor confirmed present in the target. "
                    f"Add '{parent}' to the entity scope, or discover the target with '{parent}' selected to confirm "
                    "the parents already exist."
                )
        if entity == "triggers":
            record = action.get("source_record", {})
            if not nested_name(record, ["Release", "ReleaseName", "ProcessName"]):
                errors.append(
                    f"Trigger {action.get('identity')} has no release/process name, so its target release key "
                    "cannot be resolved. Re-run discovery so trigger records include their release."
                )
            if trigger_kind(record) == "queue" and not nested_name(
                record, ["QueueDefinition", "QueueDefinitionName", "QueueName"]
            ):
                errors.append(
                    f"Queue trigger {action.get('identity')} has no queue name, so its target queue key "
                    "cannot be resolved. Re-run discovery so trigger records include their queue."
                )
        if entity == "packages":
            package_path = package_file_path(config, action.get("source_record", {}))
            if not package_path.exists():
                errors.append(f"Package file is not staged for {action.get('identity')}: {package_path}")
            elif package_path.stat().st_size == 0:
                errors.append(f"Package file is empty for {action.get('identity')}: {package_path}")
        if entity == "libraries":
            library_path = library_file_path(config, action.get("source_record", {}))
            if not library_path.exists():
                errors.append(f"Library file is not staged for {action.get('identity')}: {library_path}")
            elif library_path.stat().st_size == 0:
                errors.append(f"Library file is empty for {action.get('identity')}: {library_path}")
    # Scope-level errors repeat once per action; report each distinct problem once.
    deduped: list[str] = []
    for error in errors:
        if error not in deduped:
            deduped.append(error)
    return deduped


def apply_plan(config: dict[str, Any], plan: dict[str, Any], yes: bool, max_actions: int | None = None) -> dict[str, Any]:
    require_target(config, "apply")
    # The analysis report is the document the operator approves. Require the
    # actual report files, not only a marker in the plan, before any target write.
    require_analysis_report(plan)
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
    apply_batch_size = max(1, int(config.get("apply_batch_size", APPLY_BATCH_SIZE_DEFAULT) or APPLY_BATCH_SIZE_DEFAULT))
    queue_item_batch_size = max(
        1,
        int(config.get("queue_item_batch_size", QUEUE_ITEM_BATCH_SIZE_DEFAULT) or QUEUE_ITEM_BATCH_SIZE_DEFAULT),
    )
    interval = int(config.get("request_interval_ms", 0) or 0)
    delay_seconds = interval / 1000 if interval > 0 else 0.0
    continue_on_error = truthy(config.get("continue_on_apply_error", True))
    results: dict[str, Any] = {
        "applied_at": now_utc(),
        "apply_batch_size": apply_batch_size,
        "queue_item_batch_size": queue_item_batch_size,
        "retry_count": APPLY_RETRY_COUNT,
        "max_attempts": APPLY_MAX_ATTEMPTS,
        "commands": [],
        "failures": [],
        "manual_remediation": plan.get("manual_remediation", []),
    }
    actions = list(plan.get("actions", []))
    if max_actions is not None:
        actions = actions[:max_actions]
    resolver = TargetResolver(config)
    previous_entity: str | None = None

    def stop_after_failure(error_text: str) -> None:
        if not continue_on_error:
            abort_apply(error_text or "apply operation failed", results)

    def execute(action: dict[str, Any], command: list[str], stage: str, batch_number: int, index: int) -> bool:
        placeholder = next(
            (arg for arg in command if str(arg).startswith(TargetResolver.PLACEHOLDER_PREFIX)),
            None,
        )
        if placeholder:
            fail(f"Refusing to run a command with an unresolved target key: {placeholder}")
        ok, _, attempts, error_text = retry_apply_operation(
            lambda: run_command(command, capture=True),
            delay_seconds=delay_seconds,
        )
        base = {
            "index": index,
            "batch_number": batch_number,
            "attempts": attempts,
            "max_attempts": APPLY_MAX_ATTEMPTS,
            "entity": action["entity"],
            "identity": action["identity"],
            "stage": stage,
            "command": command,
        }
        if ok:
            results["commands"].append(base)
            return True
        failure = dict(base)
        failure["reason"] = error_text or LAST_FAILURE_DETAIL or "apply operation failed"
        results["failures"].append(failure)
        stop_after_failure(str(failure["reason"]))
        return False

    def record_build_failure(action: dict[str, Any], stage: str, batch_number: int, index: int, attempts: int, error_text: str) -> None:
        failure = {
            "index": index,
            "batch_number": batch_number,
            "attempts": attempts,
            "max_attempts": APPLY_MAX_ATTEMPTS,
            "entity": action["entity"],
            "identity": action["identity"],
            "stage": stage,
            "command": "build apply command",
            "reason": error_text or LAST_FAILURE_DETAIL or "could not build apply command",
            "command_build_failed": True,
        }
        results["failures"].append(failure)
        stop_after_failure(str(failure["reason"]))

    def build_commands(action: dict[str, Any], stage: str, batch_number: int, index: int) -> list[list[str]] | None:
        ok, commands, attempts, error_text = retry_apply_operation(
            lambda: action_command(action, config, resolver) if stage == "create" else action_post_commands(action, config, resolver),
            delay_seconds=delay_seconds,
        )
        if ok:
            return commands or []
        record_build_failure(action, stage, batch_number, index, attempts, error_text)
        return None

    def execute_rest(action: dict[str, Any], batch_number: int, index: int) -> bool:
        ok, labels, attempts, error_text = retry_apply_operation(
            lambda: rest_apply_action(action, config, resolver),
            delay_seconds=delay_seconds,
        )
        base = {
            "index": index,
            "batch_number": batch_number,
            "attempts": attempts,
            "max_attempts": APPLY_MAX_ATTEMPTS,
            "entity": action["entity"],
            "identity": action["identity"],
            "stage": "rest",
        }
        if ok:
            for label in labels or []:
                results["commands"].append(dict(base, command=label))
            return True
        failure = dict(base, command=f"{uip_family(action['entity'])} for {action['identity']}")
        failure["reason"] = error_text or LAST_FAILURE_DETAIL or "apply operation failed"
        results["failures"].append(failure)
        stop_after_failure(str(failure["reason"]))
        return False

    def execute_queue_batch(batch: list[dict[str, Any]], batch_number: int, first_index: int) -> None:
        key = queue_item_batch_key(batch[0])
        ok, labels, attempts, error_text = retry_apply_operation(
            lambda: rest_apply_queue_items(batch, config, resolver),
            delay_seconds=delay_seconds,
        )
        label = (labels or ["POST BulkAddQueueItems"])[0] if ok else "BulkAddQueueItems"
        if ok:
            # Keep one auditable success row per item even though the transport
            # uses one bulk request. This prevents a later isolated failure from
            # being confused with a batch-level failure in the report.
            for offset, member in enumerate(batch):
                results["commands"].append({
                    "index": first_index + offset,
                    "batch_number": batch_number,
                    "attempts": attempts,
                    "max_attempts": APPLY_MAX_ATTEMPTS,
                    "entity": "queue_items",
                    "identity": member["identity"],
                    "stage": "rest",
                    "command": label,
                    "bulk_batch_size": len(batch),
                })
            return

        # A bulk endpoint failure is not allowed to turn a whole batch into a
        # set of failures. Try each item once independently and record only
        # items that still fail, preserving the bulk reason when no item-level
        # reason is available.
        bulk_error = error_text or LAST_FAILURE_DETAIL or "bulk queue-item apply failed"
        for offset, member in enumerate(batch):
            item_ok, item_labels, item_attempts, item_error = retry_apply_operation(
                lambda member=member: rest_apply_queue_items([member], config, resolver),
                delay_seconds=delay_seconds,
            )
            item_base = {
                "index": first_index + offset,
                "batch_number": batch_number,
                "attempts": item_attempts,
                "max_attempts": APPLY_MAX_ATTEMPTS,
                "entity": "queue_items",
                "identity": member["identity"],
                "stage": "rest",
                "bulk_batch_size": len(batch),
                "fallback_from_bulk": True,
            }
            if item_ok:
                for item_label in item_labels or []:
                    results["commands"].append(dict(item_base, command=item_label))
                continue
            failure = dict(item_base, command="BulkAddQueueItems")
            # A bare exit code is not a more useful diagnosis than the bulk
            # error. Keep the concrete bulk reason unless the isolated call
            # returned its own meaningful CLI/API or exception text.
            item_reason = item_error if item_error and item_error != "apply operation failed" else ""
            failure["reason"] = item_reason or bulk_error
            results["failures"].append(failure)
            stop_after_failure(str(failure["reason"]))

    for batch_number, action_batch in enumerate(iter_batches(actions, apply_batch_size), start=1):
        batch_start = (batch_number - 1) * apply_batch_size
        cursor = 0
        while cursor < len(action_batch):
            action = action_batch[cursor]
            entity = action["entity"]
            # Newly created objects of the previous family are what the next
            # family resolves its keys against, so drop that family's cached lookup.
            if previous_entity and previous_entity != entity:
                resolver.invalidate(RESOLVER_FAMILY_BY_ENTITY.get(previous_entity, previous_entity))
            previous_entity = entity

            if entity == "queue_items":
                queue_batch = [action]
                key = queue_item_batch_key(action)
                probe = cursor + 1
                while (
                    probe < len(action_batch)
                    and action_batch[probe]["entity"] == "queue_items"
                    and queue_item_batch_key(action_batch[probe]) == key
                    and len(queue_batch) < queue_item_batch_size
                ):
                    queue_batch.append(action_batch[probe])
                    probe += 1
                execute_queue_batch(queue_batch, batch_number, batch_start + cursor + 1)
                cursor = probe
                continue

            index = batch_start + cursor + 1
            created = True
            if entity in REST_APPLIED_ENTITIES:
                created = execute_rest(action, batch_number, index)
            else:
                commands = build_commands(action, "create", batch_number, index)
                if commands is None:
                    created = False
                else:
                    for command in commands:
                        if not execute(action, command, "create", batch_number, index):
                            created = False
                            break
            cursor += 1
            if not created:
                continue

            # The post step keys off the object just created, so its family cache
            # must be dropped before the lookup — not after.
            if action_has_post_step(action):
                resolver.invalidate(RESOLVER_FAMILY_BY_ENTITY.get(entity, entity))
                post_commands = build_commands(action, "post-create", batch_number, index)
                if post_commands is not None:
                    for command in post_commands:
                        execute(action, command, "post-create", batch_number, index)
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
    discover_parser.add_argument("--out", help="Snapshot path; defaults to .migration-state/source.json or target.json beside the config.")

    stage_parser = sub.add_parser("stage-packages", help="Download source package .nupkg files into the staging folder.")
    stage_parser.add_argument("--config", required=True)
    stage_parser.add_argument("--discovery", help="Optional source discovery JSON to reuse instead of discovering again.")
    stage_parser.add_argument("--target-discovery", help="Optional target discovery JSON to reuse for exact package ID/version matching; otherwise target.json is reused when present or the target package family is discovered live.")
    stage_parser.add_argument("--out", help="Stage report path; defaults to .migration-state/package-stage-report.json beside the config.")

    stage_libraries_parser = sub.add_parser("stage-libraries", help="Download source tenant feed library .nupkg files into the staging folder.")
    stage_libraries_parser.add_argument("--config", required=True)
    stage_libraries_parser.add_argument("--discovery", help="Optional source discovery JSON to reuse instead of discovering again.")
    stage_libraries_parser.add_argument("--target-discovery", help="Optional target discovery JSON to reuse for exact library ID/version matching; otherwise target.json is reused when present or the target library family is discovered live.")
    stage_libraries_parser.add_argument("--out", help="Stage report path; defaults to .migration-state/library-stage-report.json beside the config.")

    plan_parser = sub.add_parser("plan", help="Generate an ordered migration plan in the machine-state directory.")
    plan_parser.add_argument("--config", required=True)
    plan_parser.add_argument("--out", help="Optional plan path; defaults to .migration-state/migration-plan.json beside the config.")
    plan_parser.add_argument("--source-discovery", help="Optional source discovery JSON to reuse instead of discovering again.")
    plan_parser.add_argument("--target-discovery", help="Optional target discovery JSON to reuse instead of discovering again.")

    render_html_parser = sub.add_parser("render-html", help="Render the reviewer HTML from an existing migration plan without discovery or target changes.")
    render_html_parser.add_argument("--config", required=True)
    render_html_parser.add_argument("--plan", required=True)
    render_html_parser.add_argument("--out", help="HTML output path; defaults to migration-analysis.html beside the config.")

    analyze_parser = sub.add_parser("analyze", help="Create the visible Excel analysis workbook and matching machine-state plan without downloading packages or applying changes.")
    analyze_parser.add_argument("--config", required=True)
    analyze_parser.add_argument("--out", help="Excel analysis workbook path; defaults to migration-analysis.xlsx beside the config.")
    analyze_parser.add_argument("--plan-out", help="Machine-readable plan path; defaults to .migration-state/migration-plan.json beside the config.")
    analyze_parser.add_argument("--source-discovery", help="Optional source discovery JSON to reuse instead of discovering again.")
    analyze_parser.add_argument("--target-discovery", help="Optional target discovery JSON to reuse instead of discovering again.")
    analyze_parser.add_argument("--html-out", help="HTML analysis report path. Always produced alongside the workbook; defaults to the --out path with a .html extension.")
    analyze_parser.add_argument("--effort-html-out", help="Compatibility alias: also write the baseline reviewer-oriented HTML report to this path.")

    apply_parser = sub.add_parser("apply", help="Apply an existing migration plan.")
    apply_parser.add_argument("--config", required=True)
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--out", help="Machine-readable results path; defaults to .migration-state/apply-results.json beside the config.")
    apply_parser.add_argument("--report-out", help="Visible audit workbook path; defaults to apply-report.xlsx beside the config.")
    apply_parser.add_argument("--max-actions", type=int, help="Apply only the first N actions from the plan for canary testing.")
    apply_parser.add_argument("--yes", action="store_true")

    check_url_parser = sub.add_parser(
        "check-url",
        help="Test whether a source URL is reachable from this machine. No config or credentials needed.",
    )
    check_url_parser.add_argument("--url", required=True, help="Orchestrator base URL.")
    check_url_parser.add_argument("--identity-url", help="Defaults to <url>/identity.")
    check_url_parser.add_argument("--out")

    probe_parser = sub.add_parser(
        "probe",
        help="Authenticate to the source and list the tenants it can see, before choosing scope.",
    )
    probe_parser.add_argument("--config", required=True)
    probe_parser.add_argument("--out")

    validate_parser = sub.add_parser("validate", help="Validate an existing migration plan.")
    validate_parser.add_argument("--config", required=True)
    validate_parser.add_argument("--plan", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-config":
        init_config_interactive(args.out, args.overwrite)
        return 0
    if args.command == "check-url":
        # No config exists at this point in intake - just a URL - so this must
        # run before the config-loading line every other subcommand shares.
        payload = check_source_reachability(args.url, args.identity_url or "")
        if args.out:
            write_json(args.out, payload)
            print(f"Wrote reachability result: {args.out}")
        print(f"Reachable: {payload['reachable']}")
        for note in payload["notes"]:
            print(f"NOTE: {note}")
        return 0 if payload["reachable"] else 1

    config = normalize_config(read_json(args.config))
    config["_config_path"] = str(Path(args.config).resolve())
    if args.command == "discover":
        payload = discover(config, args.side)
        output_path = output_path_or_state(config, args.out, f"{args.side}.json")
        write_json(output_path, payload)
        print(f"Wrote {args.side} discovery snapshot: {output_path}")
        return 0
    if args.command == "stage-packages":
        payload = stage_packages(config, args.discovery, args.target_discovery)
        output_path = output_path_or_state(config, args.out, "package-stage-report.json")
        write_json(output_path, payload)
        print(f"Wrote package stage report: {output_path}")
        return 0
    if args.command == "stage-libraries":
        payload = stage_libraries(config, args.discovery, args.target_discovery)
        output_path = output_path_or_state(config, args.out, "library-stage-report.json")
        write_json(output_path, payload)
        print(f"Wrote library stage report: {output_path}")
        return 0
    if args.command == "plan":
        payload = make_plan(config, args.source_discovery, args.target_discovery)
        payload["analysis_report"] = None
        output_path = output_path_or_state(config, args.out, "migration-plan.json")
        write_json(output_path, payload)
        print(f"Wrote migration plan: {output_path}")
        print(
            "NOTE: no analysis report was produced, so apply will refuse this plan. "
            "Run the analyze step to generate the report the operator must approve."
        )
        return 0
    if args.command == "render-html":
        plan = read_json(args.plan)
        html_path = Path(args.out) if args.out else default_visible_artifact(config, "migration-analysis.html")
        write_html_report(config, plan, html_path)
        print(f"Wrote analysis report (HTML): {html_path}")
        print("No discovery, package staging, or target changes were performed.")
        return 0
    if args.command == "analyze":
        analysis_path = Path(args.out) if args.out else default_visible_artifact(config, "migration-analysis.xlsx")
        plan_path = output_path_or_state(config, args.plan_out, "migration-plan.json")
        payload = make_plan(config, args.source_discovery, args.target_discovery)
        payload["analysis_report"] = str(analysis_path)
        html_path = Path(args.html_out) if args.html_out else analysis_path.with_suffix(".html")
        payload["analysis_report_html"] = str(html_path)
        write_json(plan_path, payload)
        write_analysis_report(config, payload, analysis_path)
        write_html_report(config, payload, html_path)
        if args.effort_html_out:
            write_effort_html_report(config, payload, args.effort_html_out)
        print(f"Wrote migration plan: {plan_path}")
        print(f"Wrote analysis report: {analysis_path}")
        print(f"Wrote analysis report (HTML): {html_path}")
        if args.effort_html_out:
            print(f"Wrote effort-planning report (HTML): {args.effort_html_out}")
        print("No package files were downloaded and no target changes were applied.")
        return 0
    if args.command == "probe":
        payload = probe_source(config)
        output_path = output_path_or_state(config, args.out, "probe-result.json")
        write_json(output_path, payload)
        print(f"Wrote probe result: {output_path}")
        print(
            f"Credentials set: {payload['credentials_present']}   "
            f"Reachable: {payload['reachable']}   Authenticated: {payload['authenticated']}"
        )
        print(f"Tenant enumeration: {payload['tenant_enumeration']}")
        for tenant in payload["tenants"]:
            print(f"  - {tenant['name']}")
        print(f"Scopes verified: {payload['scope_verified']}" )
        if payload["missing_scopes"]:
            print("Missing scopes: " + " ".join(payload["missing_scopes"]))
        for note in payload["notes"]:
            print(f"NOTE: {note}")
        return 0 if probe_succeeded(payload) else 1
    if args.command == "validate":
        errors = validate_plan(config, read_json(args.plan))
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("Plan validation passed.")
        return 0
    if args.command == "apply":
        plan = read_json(args.plan)
        results_path = output_path_or_state(config, args.out, "apply-results.json")
        report_path = Path(args.report_out) if args.report_out else default_visible_artifact(config, "apply-report.xlsx")
        try:
            payload = apply_plan(config, plan, args.yes, args.max_actions)
            persist_apply_artifacts(config, plan, payload, results_path, report_path)
            print(f"Wrote apply results: {results_path}")
            print(f"Wrote apply audit report: {report_path}")
            return 0
        except ApplyAbort as error:
            # A fail-fast stop carries the partial result object so the
            # operator receives the successes, the exact failure reason, and
            # the not-reached portion in both required artifacts.
            payload = error.results
            persist_apply_artifacts(config, plan, payload, results_path, report_path)
            print(f"Wrote partial apply results: {results_path}")
            print(f"Wrote apply audit report: {report_path}")
            return int(error.code) if isinstance(error.code, int) else 2
        except SystemExit as error:
            # Preflight failures occur before apply can construct its normal
            # result object. Preserve the engine's exact diagnostic and still
            # leave an auditable failure artifact for this apply invocation.
            message = LAST_FAILURE_DETAIL or str(error).strip() or "apply failed before target writes"
            payload = preflight_apply_failure(plan, message)
            persist_apply_artifacts(config, plan, payload, results_path, report_path)
            print(f"Wrote apply results: {results_path}")
            print(f"Wrote apply audit report: {report_path}")
            return int(error.code) if isinstance(error.code, int) else 2
    fail(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
