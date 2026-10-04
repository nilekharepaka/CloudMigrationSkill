# Migration Engine CLI Reference

Canonical syntax for `uip_cloud_migration.py`. This file is the only source of truth for subcommands, flags, config fields, and artifact names.

## Artifact layout

The primary operator deliverable is one Excel workbook per phase:

- `migration-analysis.xlsx` — read-only decision workbook with summary, readiness, plan, and one `Entity - <family>` tab per selected entity family.
- `apply-report.xlsx` — per-run audit workbook with summary, failures, remediation, not-attempted items, and one `Apply - <family>` tab per selected entity family.

Machine-readable JSON is still required for resume and validation, but it is state rather than the operator-facing report. When output flags are omitted, snapshots, plans, probe results, stage reports, and apply results are written below `.migration-state` beside the config. Explicit `--out`/`--plan-out` paths remain supported for compatibility. `analyze` also writes the companion HTML analysis report beside the workbook by default; use `--html-out` to choose another path, or `--effort-html-out` to write a compatibility copy.

**Engine path:** `{SKILL_DIR}/scripts/uip_cloud_migration.py`, where `{SKILL_DIR}` is the active Autopilot skill directory.

Throughout this reference, `$MIG` stands for that path. Set it once per shell:

```powershell
$MIG = "$HOME\.autopilot\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py"
```

```bash
MIG="{SKILL_DIR}/scripts/uip_cloud_migration.py"
```

**Requirements:** Python 3.10 or newer (the engine uses `X | Y` type syntax). Standard library only — no `pip install` step. The `uip` CLI must be on `PATH` for Cloud-target operations.

**Target write paths.** Most target writes are `uip` CLI calls. Four things go through the Orchestrator OData API instead, because no CLI flag exposes them: calendar excluded dates, credential store creation, queue item content, and bucket file content. The API path gets its bearer token from `uip login refresh --output json` (the documented machine-consumption contract, which guarantees validity for a stated window). No second External Application is needed for the target, and the token is held in memory only — never written to any artifact.

**Entity families.** Eighteen configuration families plus two content families, `bucket_files` and `queue_items`, which are planned last because they depend on their parent bucket or queue existing.

---

## Subcommand Summary

| Subcommand | Reads | Writes locally | Writes to target |
|---|---|---|---|
| `check-url` | a raw URL — no config, no credentials | nothing (unless `--out`) | No |
| `init-config` | operator input | config file | No |
| `probe` | source (credentials, if reachable) | nothing (unless `--out`) | No |
| `discover` | source or target | discovery snapshot | No |
| `analyze` | source + target | analysis workbook + plan | No |
| `plan` | source + target | plan | No |
| `stage-packages` | source feed | `.nupkg` files | No |
| `stage-libraries` | source feed | `.nupkg` files | No |
| `validate` | target + plan | nothing | No |
| `apply` | target + plan | results file | **Yes** |

---

## `check-url`

Tests whether a source URL is reachable from this machine. No config file, no credentials — this exists specifically to be usable the instant the operator states a base URL, before Identity URL, scopes, or credential variable names are even asked about.

```powershell
python $MIG check-url --url https://uipath.contoso.local
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `--url` | Yes | — | Orchestrator base URL |
| `--identity-url` | No | `<url>/identity` | Override if the Identity URL differs from the default derivation |
| `--out` | No | — | Write the result as JSON instead of just printing it |

Sends a single unauthenticated GET to `<identity-url>/.well-known/openid-configuration` with a 10-second timeout. **Any HTTP response counts as reachable — even an error status.** A 403 or 404 still proves the network path works; only a connection-level failure (DNS resolution, connection refused, timeout) means the host was not reached. Exit code is `0` when reachable, `1` when not — script-friendly for a pre-flight check before the rest of intake.

```powershell
# Exit code doubles as a yes/no
python $MIG check-url --url https://uipath.contoso.local && echo "reachable"
```

---

## `probe`

Authenticates to the source and lists the tenants it can see, before a tenant is chosen. Needs a config (so `orchestrator_url`, `identity_url`, `scope`, and the two credential env var names must already be set), and the credential values must be set in the current shell.

```powershell
python $MIG probe --config migration.local.json --out probe-result.json
```

| Flag | Required | Meaning |
|---|---|---|
| `--config` | Yes | Config path |
| `--out` | No | Write the result as JSON instead of just printing it |

Checks three independent things, in this order, and stops at the first failure:

1. **Reachability** — the same unauthenticated check as `check-url`, run first and unconditionally. This never depends on credentials, so a missing environment variable is never mistaken for an unreachable host, and vice versa.
2. **Credentials present** — are the two configured environment variable names actually set in the process environment? Checked locally; no network call. If an assistant is running this check on the operator's behalf, this means set persistently (e.g. `setx` on Windows) — a session-scoped variable the operator set in their own terminal is invisible to a command the assistant runs, since each is typically a fresh process.
3. **Authenticated** — does the client-credentials token request succeed?

If reachable and authenticated, attempts tenant enumeration (`OR.Administration` scope, host-scoped application). A tenant-scoped application will see this refused or empty — that is reported as informational, not as failure, since discovery itself never needs this endpoint.

Result fields: `reachable`, `credentials_present`, `authenticated`, `scopes_requested`, `scopes_granted[]`, `missing_scopes[]`, `scope_verified`, `token_expires_in`, `tenant_enumeration` (`ok` / `empty` / `refused` / `not attempted`), `tenants[]`, `notes[]`. The probe command exits successfully only when reachability, credentials, authentication, and complete permission verification all pass; an empty or refused tenant enumeration is informational and does not fail an otherwise verified probe.

---

## `init-config`

Interactive setup. Writes tenant names, URLs, folder scope, entity choices, environment variable *names*, and pacing options. Never writes secret values.

```powershell
python $MIG init-config --out migration.local.json
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `--out` | No | `migration.local.json` | Config output path |
| `--overwrite` | No | off | Replace an existing config file |

Prompts include a **target existence** question — answer no for a pre-provisioning assessment.

Every scope question is asked regardless of the migration mode. `lift_and_shift` and `assessment_only` pre-fill folder and entity scope as open, but the operator still confirms them — that confirmation is what catches an operator who said lift-and-shift and meant something narrower.

Prompts run in three labelled blocks, source connection first:

**Block 1 — source connection:** deployment type (`msi_standalone` \| `automation_suite` \| `cloud_tenant`), source Orchestrator base URL, tenant name, and for direct REST sources the derived/override Identity URL, one combined confirmation that the External Application exists with the complete required permission set, and the two credential environment-variable **names**. Authentication is derived automatically: `msi_standalone` and `automation_suite` use `direct_rest`; `cloud_tenant` uses the existing `uip` session. The fixed direct REST permission set is `OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration OR.Jobs OR.Users OR.Robots OR.Machines OR.Webhooks OR.License`; the token/probe verifies the granted subset and never prints the token. `init-config` never stores secret values.

**Block 2 — scope:** migration mode, folder paths (blank = all), entity families (blank = all).

**Block 3 — target and policy:** target-exists confirmation, target tenant, target folder paths, package staging folder, request interval, batch size, four continue-on-error toggles, dummy credential password, temporary webhook secret.

---

## `discover`

Read-only inventory of one side.

```powershell
# Defaults write source.json and target.json under .migration-state beside the config.
python $MIG discover --config migration.local.json --side source
python $MIG discover --config migration.local.json --side target
```

| Flag | Required | Meaning |
|---|---|---|
| `--config` | Yes | Config path |
| `--side` | Yes | `source` or `target` |
| `--out` | No | `.migration-state/<side>.json` beside the config | Snapshot output path |

---

## `analyze`

Discovery + diff + Excel workbook + machine-readable plan, in one read-only pass. This is the phase the operator approves.

```powershell
# Writes migration-analysis.xlsx beside the config and machine state under .migration-state.
python $MIG analyze --config migration.local.json
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `--config` | Yes | — | Config path |
| `--out` | No | `migration-analysis.xlsx` beside the config | Visible Excel analysis workbook path |
| `--plan-out` | No | `.migration-state/migration-plan.json` beside the config | Machine-readable plan path |
| `--source-discovery` | No | — | Reuse a source snapshot instead of re-discovering |
| `--target-discovery` | No | — | Reuse a target snapshot instead of re-discovering |
| `--html-out` | No | `--out` with a `.html` extension | Path for the companion baseline HTML report |
| `--effort-html-out` | No | — | Compatibility alias: also write the baseline reviewer-oriented HTML report to this path |

**Two reports, always, from one command.** `analyze` writes the Excel workbook and the baseline HTML report every time. Both render from the identical classification (`build_report_model`), so they cannot disagree on a count or verdict. The workbook is for filtering and sorting row by row; the baseline HTML report is an offline, viewer-safe reviewer report with ordinary anchor navigation and six always-visible sections: **Review workspace**, **Executive summary**, **Effort & blockers**, **Entity inventory**, **Human work details**, and **Migration plan**. The review workspace is local-only: it records reviewer status, ownership, dates, comments, and row-level notes, but does not edit the plan, change scope, call UiPath, or authorize target writes. The report exposes source/target/planned counts and all four disposition categories per entity, lists every hybrid/prerequisite/manual action by identity with its required human action and timing, and provides owner/hours placeholders for customer effort estimation. It does not invent duration estimates. Use **Print / Save as PDF** for a complete printable report.

If `--effort-html-out <path>` is supplied, `analyze` writes a second copy of the same baseline reviewer report at that path for compatibility with earlier invocations.

The headline **no-human-intervention coverage** is `automatic actions ÷ planned actions × 100`. Planned actions exclude skipped records because those already exist in the target.

**Source-only assessment.** With no target configured, `analyze` skips target discovery entirely. The report says so on its first sheet, the target counts read `n/a`, the `Skipped` sheet explains that nothing can be detected as already existing, and the verdict is framed as `ASSESSMENT - N issues would block a future apply` rather than `BLOCKED`. Everything else — dispositions, per-entity deep dive, prerequisites, volume sizing — is fully meaningful.

### Workbook sheets

| Sheet | Answers |
|---|---|
| `Executive Summary` | Readiness verdict, declared migration mode, and the four disposition counts |
| `Readiness and Blockers` | What stops apply, what an operator must do, and what merely needs noting |
| `Disposition by Entity` | Per family: automatic / hybrid / manual-prerequisite / manual-only / skipped, plus a verdict |
| `Entity Deep Dive` | Per family: scope, write path, dependencies, what migrates, what stays behind, prerequisites, post-actions, principal risk |
| `Planned Actions` | Every action with its disposition, why, who does what, and when |
| `Manual Prerequisites` | Must exist in the target **before** apply |
| `Hybrid Follow-Ups` | Migrates, then needs a human step |
| `Manual Only` | No create endpoint — hand-build |
| `Skipped` | Not planned, with the reason |
| `Not Migrated by Design` | Explicitly out of scope, with the reason |
| `Post-Migration Remediation` | The closing checklist |
| `Apply Sequence` | Dependency order with the gate before each step |
| `Approval Runbook` | The gates still outstanding |

The readiness verdict is one of `READY`, `READY WITH ACTIONS`, or `BLOCKED`. A `Blocks apply` value of `Yes` always carries severity `Blocker`.

The workbook is written with the standard library, not `openpyxl`. It opens in Excel, LibreOffice, and Google Sheets.

---

## `plan`

Plan only, without the workbook. Use `analyze` unless a workbook is explicitly unwanted.

```powershell
# Plan-only output is machine state under .migration-state; use analyze for the workbook.
python $MIG plan --config migration.local.json
```

| Flag | Required | Meaning |
|---|---|---|
| `--config` | Yes | Config path |
| `--out` | No | `.migration-state/migration-plan.json` beside the config | Plan output path |
| `--source-discovery` | No | Reuse a source snapshot |
| `--target-discovery` | No | Reuse a target snapshot |

---

## `stage-packages`

Downloads source package `.nupkg` files into the configured staging folder. Run only after the analysis report is approved.

```powershell
python $MIG stage-packages --config migration.local.json --discovery .migration-state\source.json
```

| Flag | Required | Meaning |
|---|---|---|
| `--config` | Yes | Config path |
| `--discovery` | No | Source snapshot to reuse instead of re-discovering |
| `--target-discovery` | No | Explicit target snapshot for exact package ID/version matching. If omitted, reuse `.migration-state/target.json` when present or discover only the target package family live |
| `--out` | No | `.migration-state/package-stage-report.json` beside the config | Stage report path |

## `stage-libraries`

Same shape, for tenant-feed libraries.

```powershell
python $MIG stage-libraries --config migration.local.json --discovery .migration-state\source.json
```

Host-feed libraries are not staged — they are manual review.

Before downloading a tenant-feed library, the engine checks the target inventory for an exact normalized library ID and version. Feed IDs are not compared across tenants. An exact match is reported as `already_in_target` and is never downloaded; a different or ambiguous version remains eligible for staging. If no explicit target snapshot is supplied, `.migration-state/target.json` is reused when present, otherwise only the target library family is discovered live. A target discovery error fails closed rather than assuming the target is empty.

The stage report includes `source_count`, `target_count`, `already_in_target_count`, `already_staged_count`, `downloaded_count`, `failed_count`, `target_match_source`, and per-library skip reasons.

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `--config` | Yes | — | Config path |
| `--discovery` | No | — | Source snapshot to reuse instead of re-discovering |
| `--target-discovery` | No | `.migration-state/target.json` when present | Target snapshot for exact library ID/version matching; otherwise the target library family is discovered live |
| `--out` | No | `.migration-state/library-stage-report.json` beside the config | Stage report path |

---

## `validate`

Validates a plan against the live target. Run immediately before apply, not days before.

```powershell
python $MIG validate --config migration.local.json --plan .migration-state\migration-plan.json
```

| Flag | Required | Meaning |
|---|---|---|
| `--config` | Yes | Config path |
| `--plan` | Yes | Plan path |

---

## `apply`

Creates objects in the target. **Every run — canary or full — also writes a human-readable audit workbook (`--report-out`, default `apply-report.xlsx`), unconditionally.** This is not optional output gated behind a flag: it exists specifically so a canary or a full apply leaves behind a per-entity record an operator can open in Excel and audit, independent of the machine-readable `--out` results file. See "Apply audit workbook" below for its sheets.

Refuses to run unless **all** of these hold:

1. The plan was generated by `analyze`, so an analysis report exists (`analysis_report` is set in the plan). A plan from bare `plan` is rejected - the analysis is mandatory and the operator must have had something to approve.
2. `--yes` is passed, as explicit operator confirmation.
3. The plan validates clean against the live target.
4. The plan contains no manual-review actions.
5. A target tenant is configured.

```powershell
# Canary — one action, then inspect the target
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --max-actions 1 --yes
```

```powershell
# Escalate after inspection
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --max-actions 25 --yes
```

```powershell
# Full apply, only after the canary is verified
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --yes
```

| Flag | Required | Meaning |
|---|---|---|
| `--config` | Yes | Config path |
| `--plan` | Yes | Plan path |
| `--yes` | Yes | Explicit operator confirmation; apply refuses without it |
| `--max-actions <N>` | No | Apply only the first N actions — the canary control |
| `--out` | No | Apply results path (machine-readable JSON) |
| `--report-out` | No | Audit workbook path, default `apply-report.xlsx`. Always written — there is no flag to suppress it |

`--max-actions` applies the first N actions **in plan order**, so a canary always exercises the top of the dependency chain (folders first). Re-running with a larger N re-walks from the start; already-created objects are skipped as existing.

### Apply audit workbook

Written every time `apply` runs, canary or full, alongside the JSON results file. Same standard-library xlsx writer as the analysis workbook.

| Sheet | Contents |
|---|---|
| `Summary` | One row per entity: planned / succeeded / failed / not attempted this run, plus a `TOTAL` row |
| `Details` | One row per attempted action: entity, identity, stage, outcome, and a comment — the actual failure reason for anything that failed, not just a bare "failed" |
| `Failures` | The same rows as `Details`, filtered to `Outcome = Failed`, for fast triage without scrolling past successes |
| `Manual Remediation` | Carried from the apply results' `manual_remediation[]` — entity, identity, reason, required action |
| `Not Attempted` | Plan actions never reached this run — beyond a `--max-actions` canary limit, or skipped because an earlier dependency failed |

Queue items use bulk transport per `(folder, queue)` batch, capped at 10 items by default, but the results and audit workbook retain one row per logical item. A failed bulk request is followed by one isolated attempt per item, so only items that fail that isolated call appear in `Failures`. Result rows include `batch_number`, `attempts`, and `max_attempts`; queue fallback rows also include `fallback_from_bulk`.

---

## Config Schema

Written by `init-config`; a starter template is in [config-example.json](config-example.json).

### Top level

| Field | Type | Default | Meaning |
|---|---|---|---|
| `migration_mode` | string | `lift_and_shift` | Declared shape: `lift_and_shift`, `folder_subset`, `entity_subset`, or `assessment_only`. Analysis fails readiness when the resolved scope contradicts it |
| `source` | object | — | Source side config, below |
| `target` | object | — | Target side config, below |
| `entities` | string[] | all 20 | Entity scope, in any order — the engine sorts to dependency order |
| `package_staging_folder` | string | `DownloadedPackages` | Where `.nupkg` files are staged |
| `request_interval_ms` | int | `250` | Sleep between calls |
| `batch_size` | int | `1000` | OData `$top` page size |
| `apply_batch_size` | int | `10` | Maximum number of ordered apply actions in one execution batch; batches remain sequential |
| `continue_on_entity_error` | bool | `true` | Keep going when one entity's discovery fails |
| `continue_on_folder_error` | bool | `true` | Keep going when one folder call fails |
| `continue_on_package_error` | bool | `true` | Keep going when one download fails |
| `continue_on_apply_error` | bool | `true` | Keep going when one apply action fails |
| `continue_on_library_error` | bool | `true` | Keep going when one library download fails |
| `credential_asset_password_mode` | string | `dummy` | Credential asset password handling |
| `dummy_credential_password` | string | `DummyPassword` | Placeholder written to credential assets |
| `webhook_dummy_secret` | string | `RotateThisWebhookSecret` | Placeholder webhook signing secret |
| `auto_import_users` | bool | absent (off) | Enables user import in the target |
| `auto_apply_external_storage_buckets` | bool | absent (off) | Leave off — provider secrets do not migrate |
| `target_credential_store_key` | string | absent | **Required when credential or secret assets are in scope.** Target credential-store key every credential asset is created against |
| `credential_store_key_map` | object | absent | Per-store override: source store name (or ID) → target store key. Takes precedence over the single default |
| `package_feed_id` | string | absent | Target feed ID for package uploads, when not the tenant default |
| `library_feed_id` | string | absent | Target feed ID for library uploads, when not the tenant default |
| `queue_item_batch_size` | int | `10` | Queue items sent per bulk call. Lower it if the target rejects large batches |

Credential stores are created through the Cloud API as definitions only; provider secrets and protected configuration never travel. Credential assets still need a resolvable target store. Resolution order: `credential_store_key_map` by source store name, then by source store ID, then a live lookup of the source store's name in the target, then `target_credential_store_key`. If none resolve, validation fails before apply rather than mid-run.

### `source`

| Field | Meaning |
|---|---|
| `auth_mode` | `direct_rest` (default for On-Prem / Automation Suite) or `uip` |
| `tenant` | Source tenant name |
| `identity_url` | Source Identity base URL — required for `direct_rest` |
| `orchestrator_url` | Source Orchestrator base URL — required for `direct_rest` |
| `client_id_env` | **Name** of the env var holding the External App client ID |
| `client_secret_env` | **Name** of the env var holding the External App client secret |
| `scope` | Space-separated Orchestrator scopes requested at token time |
| `folder_paths` | Fully-qualified folder paths; empty = all discoverable |
| `uip_extra_args` | Extra args appended to `uip` calls when `auth_mode` is `uip` |

### `target`

| Field | Meaning |
|---|---|
| `authority` | Cloud authority/base URL supplied during intake, such as `https://cloud.uipath.com` or `https://staging.uipath.com` |
| `organization` | Exact target Cloud organization supplied during intake; required for a Cloud target |
| `tenant` | Exact target tenant name supplied during intake; required for a migration target |
| `uip_profile` | Optional named `uip` profile. When blank, the engine derives a safe profile from organization and tenant |
| `auth_mode` | Omit for a Cloud target driven by `uip`; set `direct_rest` plus URLs and env-var names for an On-Prem target |
| `mode` | Set to `none` when no target tenant exists yet. `analyze` and `plan` then skip target discovery and produce a source inventory; `stage-*`, `validate` and `apply` refuse to run. An empty `tenant` has the same effect |
| `folder_paths` | Target folder scope; defaults to the source scope |
| `uip_extra_args` | Extra args appended to `uip` calls |

For every Cloud-target operation, the engine ignores the ambient/default `uip` session as destination truth. It performs a destination-specific interactive login into the configured named profile with `--authority`, `--organization`, and `--tenant`, verifies the returned organization, tenant, and authority host, and then binds subsequent `uip` commands to that profile. Inherited `UIPATH_CLI_*` environment-auth variables are removed from child `uip` processes so a stale Studio tenant cannot override intake. The same verified profile is used by `uip login refresh` for the Cloud REST fields that the CLI does not expose. A failed or mismatched login is a hard stop; the engine never falls back to the current session.

**No config field ever holds a secret value.** `client_id_env` and `client_secret_env` hold variable *names*.

### Recommended source External App scopes

```text
OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration OR.Jobs OR.Users OR.Robots OR.Machines OR.Webhooks OR.License
```

### Setting source credentials

**Run these yourself, directly in your own terminal — never paste a command containing the real value into a chat session for an assistant to run.** If a secret value ever ends up in a conversation, treat it as compromised and rotate it, regardless of what happens next.

**A human typing every command in one continuous terminal** can use a session-scoped variable:

```powershell
$env:UIP_ONPREM_CLIENT_ID = "<source external app id>"
$env:UIP_ONPREM_CLIENT_SECRET = "<source external app secret>"
```

```bash
export UIP_ONPREM_CLIENT_ID="<source external app id>"
export UIP_ONPREM_CLIENT_SECRET="<source external app secret>"
```

**An assistant running these commands on your behalf** needs a persistent variable instead — each command it runs is typically a fresh process, so a session-scoped one set in your terminal is invisible to it:

```powershell
setx UIP_ONPREM_CLIENT_ID "<source external app id>"
setx UIP_ONPREM_CLIENT_SECRET "<source external app secret>"
```

`setx` writes to the persistent user environment, so it takes effect for processes started *after* you run it — not the terminal you ran it in. Open a new terminal, or simply tell the assistant it is set; its next command is a new process regardless.

---

## Cloud Target Login

```bash
uip login status --output json
```

```bash
uip login tenant list --output json
```

```bash
uip login tenant set "<target-cloud-tenant>" --output json
```

For a non-default environment:

```bash
uip login --authority https://cloud.uipath.com --tenant "<target-cloud-tenant>"
```

---

## Plan Schema

| Field | Meaning |
|---|---|
| `migration_plan_version` | Engine plan version; `validate` rejects a mismatch |
| `generated_at` | UTC timestamp |
| `migration_mode` | The shape the operator declared at intake |
| `target_diffed` | `false` when no target existed, so nothing could be detected as already present |
| `analysis_report` | Path to the workbook generated with this plan, or `null` for a plan from bare `plan`. Apply refuses a plan where this is `null` |
| `entities` | Entity scope this plan was generated for |
| `dependency_order` | The canonical order actions are sorted into |
| `actions[]` | Ordered work items |
| `actions[].entity` | Canonical entity name |
| `actions[].identity` | Human-readable key used in logs and errors |
| `actions[].operation` | `create`, `download_upload` (packages and libraries), or `manual_review` |
| `actions[].requires_manual_mapping` | True when the action cannot be auto-applied |
| `actions[].uip_family` | Which `uip` command family executes it |
| `actions[].notes[]` | Per-action warnings — the only place some limitations appear |
| `actions[].disposition` | `Automatic`, `Hybrid — applied, then manual follow-up`, `Manual prerequisite — create in target BEFORE apply`, or `Manual only — no create endpoint` |
| `actions[].disposition_reason` | Why it landed in that disposition |
| `actions[].operator_action` | What a human must do, if anything |
| `actions[].operator_action_timing` | When they must do it |
| `actions[].source_record` | The discovered source record the action was built from |
| `actions[].permissions[]` | Roles only: permission names granted by the follow-up update |
| `actions[].trigger_kind` | Triggers only: `time`, `queue`, or `api` as classified at plan time |
| `actions[].excluded_date_count` | Calendars only: number of excluded dates included in the calendar create payload; verify in the target |
| `actions[].package_staging_folder` | Packages and libraries only: where the binary is expected |
| `actions[].credential_asset_password_mode` / `dummy_password` | Credential assets only |
| `skipped[]` | Records not planned — `entity`, `identity`, `reason` |
| `manual_remediation[]` | Post-apply operator work — `entity`, `identity`, `reason`, `required_action` |
| `source_summary` / `target_summary` | Per-entity record counts on each side |
| `readiness[]` | Cross-entity findings — `severity`, `area`, `finding`, `impact`, `resolution`, `blocks_apply`. Same content as the workbook's readiness sheet |

`manual_remediation` reasons: `automatic_apply_not_implemented`, `credential_asset_dummy_password`, `webhook_temporary_signing_secret`, `credential_store_secret_not_migratable`, `role_permissions_unknown`. Calendar excluded dates are included in the calendar create payload and require target verification rather than manual re-entry.

### Apply results schema

| Field | Meaning |
|---|---|
| `applied_at` | UTC timestamp |
| `commands[]` | Executed commands — `entity`, `identity`, `stage`, `command` |
| `failures[]` | Actions that failed — same shape. Populated when continue-on-error is on |
| `manual_remediation[]` | Carried through from the plan |

`stage` is `create` (a `uip` CLI call), `post-create` (a follow-up keyed on the new object — a role's permission grant), or `rest` (an Orchestrator API call). A role appears twice: once for the create, once for the permission grant. A queue-item batch appears once for the whole batch, with an identity of the form `<folder>/<queue> xN`.

---

## Artifacts

| Artifact | Default name | Contents |
|---|---|---|
| Config | `migration.local.json` | Tenant names, URLs, scope, env var names, pacing |
| Source snapshot | `.migration-state/source.json` | Full source inventory; machine-readable state |
| Target snapshot | `.migration-state/target.json` | Full target inventory; machine-readable state |
| Analysis workbook | `migration-analysis.xlsx` | Operator decision workbook with core review sheets and one `Entity - <family>` tab per selected family |
| Analysis HTML | `migration-analysis.html` | Companion reviewer report rendered from the same analysis classification |
| Plan | `.migration-state/migration-plan.json` | Ordered actions, skips, readiness findings, and manual remediation |
| Package stage report | `.migration-state/package-stage-report.json` | What was downloaded, and failures |
| Library stage report | `.migration-state/library-stage-report.json` | Same, for tenant-feed libraries |
| Apply results | `.migration-state/apply-results.json` | Commands executed and their outcomes (machine-readable) |
| Apply audit workbook | `apply-report.xlsx` | Per-entity success/failure counts, common audit sheets, and one `Apply - <family>` tab per selected family — written unconditionally on every apply |
| Staged binaries | `DownloadedPackages/` | `.nupkg` files |

All of these are operator-local. None of them may be committed. The repository `.gitignore` excludes them.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `migration.local.json already exists` | Config present | Add `--overwrite`, or point `--out` elsewhere |
| `direct_rest auth requires identity_url` | Source URLs missing from config | Re-run `init-config` with complete intake |
| `direct_rest auth requires orchestrator_url` | Same | Same |
| Token request returns `invalid_client` | Env vars not set in the process environment, or wrong app | Set the vars persistently (`setx` on Windows) if an assistant runs the commands — a session-scoped variable set in the operator's own terminal is invisible to a separate process |
| Token request returns `invalid_scope` | External App missing a scope the entity set needs | Add the scope to the app, re-run |
| `401` on source calls | Token expired or wrong tenant header | Confirm the source tenant name in the config |
| `403` on folder-scoped source calls | External App lacks folder access | Grant the app access to the folders in scope |
| Every folder-scoped entity has zero records | `folder_paths` matched no source folder | Clear `folder_paths`, re-discover, read real `FullyQualifiedName` values from the snapshot, then narrow |
| `No direct_rest endpoint registered for <entity>` | Entity not supported on the direct REST path | Remove it from `entities` for this run |
| `Unexpected plan version` | Plan built by a different engine version | Regenerate the plan; never edit the version |
| `Refusing to apply without --yes` | Missing confirmation | Add `--yes` after the operator approves |
| `Plan validation failed before apply` | Target drifted since plan generation | Re-run `analyze`, re-validate |
| `Plan contains manual_review actions…` | Manual entities mixed into the plan | Narrow `entities`, regenerate, handle manual items separately |
| `Plan contains actions that direct_rest apply cannot handle` | Plan exceeds the direct REST apply set | Narrow `entities` for the On-Prem target |
| Package apply fails on a missing file | Binaries not staged, or staged elsewhere | Run `stage-packages`; confirm `package_staging_folder` matches |
| Process apply fails after packages succeeded | Package version not yet resolvable in the target feed | Confirm packages landed, then re-run |
| `Cannot resolve target process (release) '<name>'` | The trigger's process is not in the target yet, or its name differs | Apply processes first; confirm the process name matches between tenants |
| `Cannot resolve target queue '<name>'` | Queue trigger applied before its queue | Apply queues first |
| `Trigger … has no release/process name` (validation) | Discovery did not expand the trigger's release | Re-run discovery — the engine expands `Release`, `QueueDefinition`, and `Calendar` |
| `Credential asset … has no resolvable target credential store` | No store mapping configured | Set `target_credential_store_key` to a store key that exists in the target |
| `Refusing to run a command with an unresolved target key` | A placeholder reached apply | Report it — apply should never build placeholders; re-run validation |
| Custom role arrives with no permissions | Source discovery returned none, or the grant failed | Check the plan's `permissions` field and the apply results for a failed `post-create` stage |
| Calendar excluded dates need verification | Target calendar creation uses the Orchestrator API to include them | Compare the target calendar with the plan count before enabling dependent triggers |
| A tenant setting was not applied | Setting carries a secret or points at the source deployment | Intentional — set it deliberately in the target |
| `Migration mode is lift-and-shift, but folder scope is narrowed` | Config contradicts the declared shape | Clear the folder scope, or change the mode to a targeted one |
| `Migration mode is lift-and-shift, but N entity families are out of scope` | Same | Add the families, or change the mode |
| `Migration mode is folder_subset, but no folder paths are named` | Targeted mode with an open scope | Name the folders, or switch to `lift_and_shift` |
| `Cannot validate a plan: no target tenant is configured` | Source-only assessment config | Expected. Set the target tenant once it exists |
| `Cannot apply: no target tenant is configured` | Source-only assessment config | Expected — apply has nowhere to write |
| `Refusing to apply: this plan was not generated with an analysis report` | Plan came from bare `plan`, not `analyze` | Re-generate with `analyze`, review the report, then apply |
| `Could not obtain a Cloud access token from uip login refresh` | Not logged in to the target, or the session expired | `uip login`, select the target tenant, retry |
| `Cloud REST POST /odata/... failed (401)` | Token expired mid-run | Re-run apply; the engine re-requests a token, and applied objects are skipped |
| `Cloud REST POST /odata/... failed (403)` | The logged-in user lacks the Orchestrator permission for that entity | Grant the permission in the target, or drop that entity from scope |
| `Cannot resolve target storage bucket '<name>'` | Bucket files planned before their bucket exists | Apply storage buckets first |
| `Source did not return a read URI` | Bucket file listing is stale, or the file was deleted at the source | Re-run discovery |
| Queue items duplicated after a re-run | The target queue does not enforce unique references | Enable unique reference on the queue before migrating items, or clear and re-migrate |
| Bucket file upload fails on large files | Pre-signed URI expired mid-upload | Re-run apply for the remaining files; uploaded files are skipped |
| `uip` command not found | CLI not on `PATH` | Install the `uip` CLI |
| `SyntaxError` on `X \| Y` type hints | Python older than 3.10 | Upgrade Python |
| Discovery is very slow | Large tenant | Raise `batch_size`, lower `request_interval_ms` cautiously, or narrow folder scope |
| Discovery triggers source throttling | Interval too aggressive | Raise `request_interval_ms` |
| A genuinely unreachable direct_rest source used to take 30-40 minutes to fail | Every folder-scoped entity independently retried its own token acquisition (3 attempts x 60s timeout each), so the cost multiplied by entity count x folder count | Fixed: token failure is now cached per side after 3 attempts (15s timeout each), so only the first call pays the real retry cost and every subsequent call in the same run fails instantly. A genuinely-down source is now confirmed in well under a minute, not tens of minutes |
