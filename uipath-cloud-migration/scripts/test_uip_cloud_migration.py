#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "uip_cloud_migration.py"


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, check=False)


def workbook_sheet_names(path: Path) -> list[str]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    return [node.attrib["name"] for node in root.findall("main:sheets/main:sheet", namespace)]


def workbook_xml_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )


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
        write_json(fixtures / "source-triggers.json", [{"Name": "InvoiceDaily", "Type": "time", "Cron": "0 0 9 ? * 1-5", "TimeZone": "UTC", "ReleaseKey": "release-key", "ProcessName": "InvoiceBot", "FolderPath": "Finance"}])
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
            "target_credential_store_key": "fixture-credential-store",
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

        analysis_xlsx = base / "migration-analysis.xlsx"
        analysis_plan = base / "analysis-plan.json"
        analysis_html = base / "migration-analysis.html"
        effort_html = base / "migration-effort-planning.html"
        analyze_result = run(
            "analyze",
            "--config", str(config),
            "--out", str(analysis_xlsx),
            "--plan-out", str(analysis_plan),
            "--html-out", str(analysis_html),
            "--effort-html-out", str(effort_html),
        )
        assert analyze_result.returncode == 0, analyze_result.stderr
        assert analysis_xlsx.exists(), "analyze must write the Excel report"
        assert analysis_html.exists(), "analyze must write the companion HTML report"
        analysis_sheets = workbook_sheet_names(analysis_xlsx)
        for expected_sheet in (
            "Read Me", "Executive Summary", "Disposition by Entity",
            "Entity - folders", "Entity - assets", "Entity - queues",
            "Entity - packages", "Entity - processes", "Entity - triggers",
        ):
            assert expected_sheet in analysis_sheets, f"missing analysis sheet: {expected_sheet}"
        assert len(analysis_sheets) == 1 + 13 + 6, analysis_sheets
        analysis_xml = workbook_xml_text(analysis_xlsx)
        assert "Source data (sanitized)" in analysis_xml
        assert "[REDACTED]" in analysis_xml
        assert "DOMAIN\\\\svc" not in analysis_xml
        report_html = analysis_html.read_text(encoding="utf-8")
        assert analysis_html.stat().st_size > 0, "baseline HTML report must not be empty"
        for label in (
            "Executive summary",
            "Effort &amp; blockers",
            "Entity inventory",
            "Human work details",
            "Migration plan",
        ):
            assert label in report_html, f"missing baseline navigation label: {label}"
        for panel_id in (
            "effort-summary",
            "effort-readiness",
            "effort-entities",
            "effort-human-work",
            "effort-plan",
        ):
            assert f'href="#{panel_id}"' in report_html, f"missing baseline anchor: {panel_id}"
            assert f'id="{panel_id}"' in report_html, f"missing baseline panel: {panel_id}"
        assert report_html.count('<section class="panel"') == 6
        assert '<script' in report_html.lower(), "review workspace script should be embedded locally"
        assert 'https://' not in report_html.lower(), "review report must not load remote scripts or styles"
        assert 'binarys' not in report_html.lower()
        assert 'class=>' not in report_html
        assert 'Owner / hours' in report_html
        assert "Migration Effort Planning Report" in report_html
        planned_count = len(payload["actions"])
        automatic_count = sum(1 for action in payload["actions"] if action.get("disposition") == "Automatic")
        human_count = planned_count - automatic_count
        assert automatic_count + human_count == planned_count
        assert f"{human_count} named actions" in report_html
        assert "Entity inventory and disposition" in report_html

        assert effort_html.exists(), "compatibility effort-planning HTML report must be written when requested"
        effort_report_html = effort_html.read_text(encoding="utf-8")
        assert effort_report_html == report_html, "compatibility effort output must match the baseline report"

        spec = importlib.util.spec_from_file_location("uip_cloud_migration", SCRIPT)
        assert spec and spec.loader
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        defaults = engine.default_migration_config()
        assert defaults["apply_batch_size"] == 10
        assert defaults["queue_item_batch_size"] == 10
        assert engine.APPLY_RETRY_COUNT == 0
        assert engine.APPLY_MAX_ATTEMPTS == 1
        assert engine.source_auth_mode("msi_standalone") == "direct_rest"
        assert engine.source_auth_mode("automation_suite") == "direct_rest"
        assert engine.source_auth_mode("cloud_tenant") == "uip"
        assert engine.ENTITY_ORDER == [
            "folders", "credential_stores", "roles", "users", "machines",
            "robots", "environments", "assets", "queues", "storage_buckets",
            "calendars", "webhooks", "feeds", "settings", "packages",
            "libraries", "processes", "triggers", "bucket_files", "queue_items",
        ]
        assert engine.ENTITY_PHASE_BY_ENTITY["packages"] == "binaries"
        assert engine.ENTITY_PHASE_BY_ENTITY["queue_items"] == "content"
        sequence = engine.apply_sequence_items(
            ["folders", "packages", "libraries", "processes", "queue_items"], []
        )
        sequence_by_entity = {item["entity"]: item for item in sequence}
        assert sequence_by_entity["packages"]["phase_label"] == "Late binary reconciliation"
        assert sequence_by_entity["queue_items"]["phase_label"] == "Final content transfer"

        # Package staging must still enforce target readiness when the source
        # snapshot has no packages; an empty source is not permission to bypass
        # the target prerequisite.
        empty_packages = base / "empty-packages.json"
        write_json(empty_packages, {"entities": {"packages": []}, "errors": []})
        try:
            engine.stage_packages(
                {"source": {"tenant": "Source"}, "target": {"mode": "absent"}},
                str(empty_packages),
            )
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("empty package staging must still require a configured target")

        # Target-aware staging must skip exact ID+version matches before any
        # source download and must still stage a source version missing in the
        # target. Feed IDs are intentionally different across tenants.
        binary_base = base / "binary-matching"
        binary_base.mkdir(parents=True, exist_ok=True)
        source_binary = binary_base / "source.json"
        target_binary = binary_base / "target.json"
        write_json(source_binary, {
            "entities": {
                "packages": [
                    {"Id": "InvoiceBot", "Version": "1.0.0"},
                    {"Key": "InvoiceBot:2.0.0"},
                ],
                "libraries": [
                    {"Id": "AutoMapper", "Key": "AutoMapper:9.0.0", "Version": "9.0.0", "LibraryFeedScope": "Tenant"},
                    {"Id": "AutoMapper", "Key": "AutoMapper:10.0.0", "Version": "10.0.0", "LibraryFeedScope": "Tenant"},
                ],
            },
            "errors": [],
        })
        write_json(target_binary, {
            "entities": {
                "packages": [{"Id": "InvoiceBot:1.0.0"}],
                "libraries": [{"Id": "AutoMapper", "Key": "AutoMapper:9.0.0", "Version": "9.0.0", "LibraryFeedScope": "Tenant", "FeedId": "different-target-feed"}],
            },
            "errors": [],
        })
        binary_config = {
            "source": {"tenant": "Source"},
            "target": {"tenant": "Target"},
            "package_staging_folder": str(binary_base / "packages"),
            "continue_on_package_error": True,
            "continue_on_library_error": True,
        }
        binary_downloads = []
        saved_binary_run_command = engine.run_command
        saved_binary_switch = engine.maybe_switch_tenant
        try:
            engine.maybe_switch_tenant = lambda *args, **kwargs: None

            def fake_binary_download(command, capture=True, env=None):
                binary_downloads.append(list(command))
                destination_index = command.index("--destination") + 1
                destination = Path(command[destination_index])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"fixture nupkg")
                return {"ok": True}

            engine.run_command = fake_binary_download
            package_report = engine.stage_packages(binary_config, str(source_binary), str(target_binary))
            assert package_report["already_in_target_count"] == 1
            assert package_report["downloaded_count"] == 1
            assert package_report["failed_count"] == 0
            assert len(package_report["skipped_packages"]) == 1
            assert package_report["skipped_packages"][0]["status"] == "already_in_target"
            assert len(binary_downloads) == 1
            assert "InvoiceBot:2.0.0" in binary_downloads[0]

            binary_downloads.clear()
            library_report = engine.stage_libraries(binary_config, str(source_binary), str(target_binary))
            assert library_report["already_in_target_count"] == 1
            assert library_report["downloaded_count"] == 1
            assert library_report["failed_count"] == 0
            assert len(library_report["skipped_libraries"]) == 1
            assert library_report["skipped_libraries"][0]["status"] == "already_in_target"
            assert library_report["skipped_libraries"][0]["match_key"] == ["automapper", "9.0.0", "tenant"]
            assert len(binary_downloads) == 1
            assert "AutoMapper:10.0.0" in binary_downloads[0]

            failed_target = binary_base / "target-with-error.json"
            write_json(failed_target, {
                "entities": {"libraries": []},
                "errors": [{"entity": "libraries", "side": "target", "message": "fixture failure"}],
            })
            try:
                engine.stage_libraries(binary_config, str(source_binary), str(failed_target))
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError("target library discovery errors must fail closed")
        finally:
            engine.run_command = saved_binary_run_command
            engine.maybe_switch_tenant = saved_binary_switch

        # The operator-facing apply workbook has the same per-entity shape as
        # the analysis workbook, while keeping machine results separate.
        audit_plan = {
            "entities": ["folders", "assets"],
            "dependency_order": ["folders", "assets"],
            "actions": [
                {"entity": "folders", "identity": "Finance", "operation": "create", "disposition": "Automatic", "source_record": {"FullyQualifiedName": "Finance"}},
                {"entity": "assets", "identity": "Finance/DbCred", "operation": "create", "disposition": "Hybrid", "source_record": {"Name": "DbCred", "Type": "Credential", "Username": "DOMAIN\\svc"}},
            ],
        }
        audit_results = {
            "commands": [{
                "entity": "folders", "identity": "Finance", "stage": "create",
                "command": "uip or folders create", "attempts": 1, "max_attempts": 1,
            }],
            "failures": [],
            "manual_remediation": [],
        }
        audit_xlsx = base / "apply-report.xlsx"
        audit_config = json.loads(config.read_text(encoding="utf-8"))
        engine.write_apply_report(audit_config, audit_plan, audit_results, audit_xlsx)
        assert audit_xlsx.exists(), "apply must write the audit workbook"
        audit_sheets = workbook_sheet_names(audit_xlsx)
        for expected_sheet in ("Read Me", "Summary", "Details", "Failures", "Manual Remediation", "Not Attempted", "Apply - folders", "Apply - assets"):
            assert expected_sheet in audit_sheets, f"missing apply sheet: {expected_sheet}"
        audit_xml = workbook_xml_text(audit_xlsx)
        assert "Source data (sanitized)" in audit_xml
        assert "[REDACTED]" in audit_xml
        assert "DOMAIN\\\\svc" not in audit_xml

        # With no explicit output flags, machine state is kept beside the
        # config under .migration-state while the workbook stays visible.
        default_base = base / "default-run"
        default_config = default_base / "migration.json"
        default_base.mkdir(parents=True, exist_ok=True)
        write_json(default_config, {
            "source": {"tenant": "Source", "folder_paths": ["Finance"]},
            "target": {"tenant": "Target", "folder_paths": ["Finance"]},
            "entities": ["folders", "assets", "queues", "packages", "processes", "triggers"],
            "package_staging_folder": str(base / "packages"),
            "request_interval_ms": 0,
            "batch_size": 1000,
            "credential_asset_password_mode": "dummy",
            "dummy_credential_password": "DummyPassword",
            "target_credential_store_key": "fixture-credential-store",
            "fixture_dir": str(fixtures),
        })
        default_analyze = run("analyze", "--config", str(default_config))
        assert default_analyze.returncode == 0, default_analyze.stderr
        assert (default_base / "migration-analysis.xlsx").exists()
        assert (default_base / "migration-analysis.html").exists()
        assert (default_base / ".migration-state" / "migration-plan.json").exists()
        assert not (default_base / "migration-plan.json").exists()
        default_discover = run("discover", "--config", str(default_config), "--side", "source")
        assert default_discover.returncode == 0, default_discover.stderr
        assert (default_base / ".migration-state" / "source.json").exists()

        assert engine.REQUIRED_SOURCE_SCOPE == (
            "OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration "
            "OR.Jobs OR.Users OR.Robots OR.Machines OR.Webhooks OR.License"
        )
        destination_config = {
            "target": {
                "authority": "https://staging.uipath.com",
                "organization": "uipathtamindia",
                "tenant": "Migration2",
            }
        }
        destination_calls = []
        saved_run_command = engine.run_command
        saved_target_context = dict(engine._TARGET_UIP_CONTEXT)
        saved_target_session_key = engine._TARGET_UIP_SESSION_KEY
        try:
            engine._TARGET_UIP_CONTEXT = {}
            engine._TARGET_UIP_SESSION_KEY = None
            destination_status = {
                "Data": {
                    "BaseUrl": "https://staging.uipath.com",
                    "OrganizationName": "uipathtamindia",
                    "TenantName": "Migration2",
                }
            }
            destination_result = {"Data": {"Message": "login complete"}}
            destination_refresh = {
                "Data": {
                    "AccessToken": "target-token",
                    "BaseUrl": "https://staging.uipath.com",
                    "OrganizationName": "uipathtamindia",
                    "TenantName": "Migration2",
                }
            }

            def fake_destination_run(command, capture=True, env=None):
                command = engine.target_uip_command(list(command))
                destination_calls.append({"command": command, "env": env})
                if "status" in command:
                    return destination_status
                if "refresh" in command:
                    return destination_refresh
                return destination_result

            engine.run_command = fake_destination_run
            verified_context = engine.ensure_target_uip_session(destination_config)
            assert verified_context == {
                "profile": "migration-uipathtamindia-migration2",
                "authority": "https://staging.uipath.com",
                "organization": "uipathtamindia",
                "tenant": "Migration2",
            }
            login_call = destination_calls[0]["command"]
            assert login_call[:2] == ["uip", "login"]
            for option, value in (
                ("--profile", "migration-uipathtamindia-migration2"),
                ("--authority", "https://staging.uipath.com"),
                ("--organization", "uipathtamindia"),
                ("--tenant", "Migration2"),
            ):
                assert login_call[login_call.index(option) + 1] == value
            status_call = destination_calls[1]["command"]
            assert status_call[:4] == ["uip", "login", "status", "--profile"] or "--profile" in status_call
            assert "migration-uipathtamindia-migration2" in status_call
            assert engine.target_uip_command(["uip", "or", "folders", "list"]) == [
                "uip", "--profile", "migration-uipathtamindia-migration2", "or", "folders", "list"
            ]
            engine.invalidate_cloud_session()
            target_session = engine.cloud_session(destination_config)
            assert target_session["token"] == "target-token"
            refresh_call = next(call for call in destination_calls if "refresh" in call["command"])
            assert "--profile" in refresh_call["command"]
            assert "migration-uipathtamindia-migration2" in refresh_call["command"]

            captured_subprocess = {}
            saved_subprocess_run = engine.subprocess.run
            try:
                stale_names = {
                    "UIPATH_CLI_ENABLE_ENV_AUTH": "true",
                    "UIPATH_CLI_TENANT_NAME": "UiPathDefault",
                    "UIPATH_CLI_TENANT_ID": "stale-tenant-id",
                    "UIPATH_CLI_ORGANIZATION_NAME": "stale-org",
                }
                os.environ.update(stale_names)

                def fake_subprocess_run(command, **kwargs):
                    captured_subprocess["command"] = command
                    captured_subprocess["env"] = kwargs.get("env")
                    return SimpleNamespace(returncode=0, stdout="{}", stderr="")

                engine.subprocess.run = fake_subprocess_run
                saved_run_command(["uip", "or", "folders", "list"])
                assert "migration-uipathtamindia-migration2" in captured_subprocess["command"]
                child_env = captured_subprocess["env"]
                assert child_env is not None
                for name in engine._TARGET_AUTH_ENV_NAMES:
                    assert name not in child_env
            finally:
                engine.subprocess.run = saved_subprocess_run
                for name in stale_names:
                    os.environ.pop(name, None)

            engine._TARGET_UIP_SESSION_KEY = None
            destination_status = {
                "Data": {
                    "BaseUrl": "https://staging.uipath.com",
                    "OrganizationName": "uipathtamindia",
                    "TenantName": "UiPathDefault",
                }
            }
            try:
                engine.ensure_target_uip_session(destination_config)
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError("destination mismatch must stop before target work")
        finally:
            engine.run_command = saved_run_command
            engine._TARGET_UIP_CONTEXT = saved_target_context
            engine._TARGET_UIP_SESSION_KEY = saved_target_session_key
        verified_probe = {
            "reachable": True,
            "credentials_present": True,
            "authenticated": True,
            "scope_verified": True,
            "missing_scopes": [],
        }
        assert engine.probe_succeeded(verified_probe)
        incomplete_probe = dict(verified_probe, scope_verified=False, missing_scopes=["OR.Users"])
        assert not engine.probe_succeeded(incomplete_probe)
        analysis_payload = json.loads(analysis_plan.read_text(encoding="utf-8"))
        report_config = json.loads(config.read_text(encoding="utf-8"))
        report_model = engine.build_report_model(report_config, analysis_payload)
        excluded_plan = dict(analysis_payload)
        excluded_plan["scope_exclusions"] = [{
            "entity": "feeds",
            "status": "excluded_by_approved_scope",
            "reason": "Source Feeds endpoint rejected the query; feeds are manual review and excluded from this automated run.",
        }]
        excluded_rows = dict(engine.make_analysis_report_rows(report_config, excluded_plan))["Not Migrated by Design"]
        feeds_row = next(row for row in excluded_rows if row[0] == "feeds")
        assert "excluded from this automated run" in feeds_row[2]
        excluded_html = engine.render_html_report(report_config, excluded_plan)
        assert "excluded from this automated run" in excluded_html
        assert "Not migrated by design" in excluded_html
        assert "feeds" in excluded_html
        expected_coverage = round(
            len(report_model["automatic"]) / report_model["planned_records"] * 100,
            1,
        ) if report_model["planned_records"] else 0.0
        assert report_model["automation_coverage_percent"] == expected_coverage
        assert f"{expected_coverage:.1f}%" in report_html

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
        assert operations[("credential_stores", "DefaultCredentialStore")] == "create"
        assert operations[("robots", "ClassicRobot1")] == "manual_review"
        assert operations[("environments", "ClassicEnvironment1")] == "manual_review"
        assert operations[("libraries", "SharedLibrary:1.0.0")] == "download_upload"
        assert operations[("feeds", "Tenant Feed")] == "manual_review"
        assert operations[("settings", "Abp.Timing.TimeZone")] == "create"
        assert extended_payload["manual_remediation"], "manual remediation should be reported for extended entities"
        extended_apply = run("apply", "--config", str(extended_config), "--plan", str(extended_plan), "--yes")
        assert extended_apply.returncode != 0, "manual_review plans must not be auto-applied"

        # The apply contract is per logical item: one attempt, immediate
        # failure reporting, bounded batches, and continuation after failure.
        assert engine.iter_batches([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
        attempts = {"count": 0}
        def succeeds_once():
            attempts["count"] += 1
            return "ok"
        ok, value, call_count, error_text = engine.retry_apply_operation(succeeds_once)
        assert (ok, value, call_count, error_text) == (True, "ok", 1, "")
        assert attempts["count"] == 1

        exhausted = {"count": 0}
        def always_fails():
            exhausted["count"] += 1
            engine.LAST_FAILURE_DETAIL = "simulated immediate failure"
            raise SystemExit(1)
        ok, value, call_count, error_text = engine.retry_apply_operation(always_fails)
        assert not ok and value is None and call_count == 1 and exhausted["count"] == 1
        assert error_text == "simulated immediate failure"

        analysis_marker = base / "retry-analysis.xlsx"
        analysis_html_marker = base / "retry-analysis.html"
        analysis_marker.write_bytes(b"analysis")
        analysis_html_marker.write_text("<html>analysis</html>", encoding="utf-8")
        queue_actions = [
            {"entity": "queue_items", "identity": "Invoices/item-1", "operation": "create", "source_record": {"QueueDefinitionName": "Invoices", "Reference": "item-1"}},
            {"entity": "queue_items", "identity": "Invoices/item-2", "operation": "create", "source_record": {"QueueDefinitionName": "Invoices", "Reference": "item-2"}},
            {"entity": "settings", "identity": "AfterQueue", "operation": "create", "source_record": {"Name": "AfterQueue", "Value": "UTC"}},
        ]
        retry_plan = {
            "analysis_report": str(analysis_marker),
            "analysis_report_html": str(analysis_html_marker),
            "actions": queue_actions,
            "manual_remediation": [],
        }
        retry_config = {
            "source": {},
            "target": {"tenant": "Target"},
            "fixture_dir": str(fixtures),
            "request_interval_ms": 0,
            "apply_batch_size": 2,
            "queue_item_batch_size": 2,
            "continue_on_apply_error": True,
        }
        engine.require_target = lambda *args, **kwargs: None
        engine.validate_plan = lambda *args, **kwargs: []
        engine.maybe_switch_tenant = lambda *args, **kwargs: None
        queue_calls = []
        def fake_queue_apply(batch, config_value, resolver):
            queue_calls.append([item["identity"] for item in batch])
            engine.LAST_FAILURE_DETAIL = "simulated queue failure"
            if len(batch) > 1 or batch[0]["identity"].endswith("item-2"):
                raise SystemExit(1)
            return ["POST BulkAddQueueItems -> Invoices (1 item(s))"]
        engine.rest_apply_queue_items = fake_queue_apply
        engine.action_command = lambda action, config_value, resolver: [["uip", "settings", "update", action["identity"]]]
        engine.run_command = lambda command, capture=True: {"ok": True}
        retry_results = engine.apply_plan(retry_config, retry_plan, yes=True)
        assert retry_results["apply_batch_size"] == 2
        assert retry_results["retry_count"] == 0
        assert retry_results["max_attempts"] == 1
        assert queue_calls == [["Invoices/item-1", "Invoices/item-2"], ["Invoices/item-1"], ["Invoices/item-2"]]
        assert len(retry_results["failures"]) == 1
        assert retry_results["failures"][0]["identity"] == "Invoices/item-2"
        assert retry_results["failures"][0]["attempts"] == 1
        assert retry_results["failures"][0]["max_attempts"] == 1
        assert retry_results["failures"][0]["reason"] == "simulated queue failure"
        assert {item["identity"] for item in retry_results["commands"]} == {"Invoices/item-1", "AfterQueue"}
        assert retry_results["commands"][-1]["identity"] == "AfterQueue"
        report_rows = engine.normalize_apply_outcomes(retry_results)
        failed_rows = [row for row in report_rows if row["outcome"] == "Failed"]
        assert len(failed_rows) == 1 and failed_rows[0]["identity"] == "Invoices/item-2"
        assert "attempts=1" in failed_rows[0]["comment"]
        assert "max_attempts=1" in failed_rows[0]["comment"]
        assert "simulated queue failure" in failed_rows[0]["comment"]
        assert "batch=1" in failed_rows[0]["comment"]
        assert "bulk_batch_size=2" in failed_rows[0]["comment"]
        assert "isolated_after_bulk_failure" in failed_rows[0]["comment"]
        success_rows = [row for row in report_rows if row["outcome"] == "Success"]
        item_one_rows = [row for row in success_rows if row["identity"] == "Invoices/item-1"]
        assert len(item_one_rows) == 1
        assert "attempts=1" in item_one_rows[0]["comment"]
        assert "max_attempts=1" in item_one_rows[0]["comment"]
        assert "batch=1" in item_one_rows[0]["comment"]
        assert "bulk_batch_size=2" in item_one_rows[0]["comment"]
        assert "isolated_after_bulk_failure" in item_one_rows[0]["comment"]

        # Fail-fast mode remains available when continuation is disabled;
        # it stops after one failed logical call, not after retries.
        queue_calls.clear()
        try:
            engine.apply_plan(dict(retry_config, continue_on_apply_error=False), retry_plan, yes=True)
        except SystemExit:
            pass
        else:
            raise AssertionError("continue_on_apply_error=False must stop after one failed item attempt")
        assert queue_calls == [["Invoices/item-1", "Invoices/item-2"], ["Invoices/item-1"], ["Invoices/item-2"]]

        # Direct-REST apply must isolate a failed logical item as well.
        # The following action proves that the next item/entity family still
        # executes after the one failed attempt.
        direct_plan = {
            "actions": [
                {"entity": "queues", "identity": "Invoices", "operation": "create", "source_record": {"Name": "Invoices"}},
                {"entity": "queues", "identity": "BrokenQueue", "operation": "create", "source_record": {"Name": "BrokenQueue"}},
                {"entity": "calendars", "identity": "AfterQueue", "operation": "create", "source_record": {"Name": "AfterQueue"}},
            ],
            "manual_remediation": [],
        }
        direct_config = {
            "target": {"auth_mode": "direct_rest", "tenant": "Target"},
            "request_interval_ms": 0,
            "apply_batch_size": 2,
            "continue_on_apply_error": True,
        }
        engine.direct_rest_folder_indexes = lambda config_value: ({}, {})
        engine.direct_rest_identity_index = lambda config_value, entity: {}
        direct_calls = []
        def fake_direct_apply(config_value, action, state):
            direct_calls.append(action["identity"])
            if action["identity"] == "BrokenQueue":
                engine.LAST_FAILURE_DETAIL = "simulated direct REST failure"
                raise SystemExit(1)
            return {"identity": action["identity"], "entity": action["entity"], "status": "created"}
        engine.direct_rest_apply_one = fake_direct_apply
        direct_results = engine.apply_plan_direct_rest(direct_config, direct_plan)
        assert direct_results["apply_batch_size"] == 2
        assert direct_results["retry_count"] == 0
        assert direct_results["max_attempts"] == 1
        assert direct_calls == ["Invoices", "BrokenQueue", "AfterQueue"]
        assert {item["identity"] for item in direct_results["actions"]} == {"Invoices", "AfterQueue"}
        assert len(direct_results["errors"]) == 1
        assert direct_results["errors"][0]["identity"] == "BrokenQueue"
        assert direct_results["errors"][0]["attempts"] == 1
        assert direct_results["errors"][0]["max_attempts"] == 1
        assert direct_results["errors"][0]["error"] == "simulated direct REST failure"
        direct_report_rows = engine.normalize_apply_outcomes(direct_results)
        direct_failed = [row for row in direct_report_rows if row["outcome"] == "Failed"]
        assert len(direct_failed) == 1 and direct_failed[0]["identity"] == "BrokenQueue"
        assert "attempts=1" in direct_failed[0]["comment"]
        assert "max_attempts=1" in direct_failed[0]["comment"]
        assert "simulated direct REST failure" in direct_failed[0]["comment"]
        assert "batch=1" in direct_failed[0]["comment"]
        direct_success = [row for row in direct_report_rows if row["outcome"] == "Success"]
        assert {row["identity"] for row in direct_success} == {"Invoices", "AfterQueue"}
        for row in direct_success:
            assert "attempts=1" in row["comment"]
            assert "max_attempts=1" in row["comment"]

        # Direct-REST fail-fast mode must stop before the later entity family
        # without repeating the failed item.
        direct_calls.clear()
        try:
            engine.apply_plan_direct_rest(
                dict(direct_config, continue_on_apply_error=False),
                direct_plan,
            )
        except SystemExit:
            pass
        else:
            raise AssertionError("direct REST continue_on_apply_error=False must stop after failure")
        assert direct_calls == ["Invoices", "BrokenQueue"]

    print("Fixture smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
