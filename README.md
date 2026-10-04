# UiPath Cloud Migration Skill

A review-first skill for assessing and migrating supported UiPath Orchestrator entities from **On-Premises Orchestrator** or **Automation Suite** to **Automation Cloud**. It can also perform supported tenant-to-tenant copies and source-only pre-migration assessments.

This package is designed for UiPath Autopilot and UiPath Studio. It is intentionally conservative: it recreates supported definitions and relationships, but it is **not a byte-identical clone** of a running Orchestrator tenant.

## What the skill does

The skill enforces a fixed, approval-gated process:

1. Collect the complete source, scope, target, and policy intake.
2. Present an execution plan and wait for approval.
3. Run read-only discovery and analysis.
4. Produce an Excel workbook, an HTML reviewer report, and a machine-readable migration plan.
5. Wait for analysis approval and clearance of readiness blockers.
6. Stage package and library binaries locally, only when approved.
7. Validate the plan against the live target.
8. Obtain separate apply approval.
9. Apply one canary action, inspect the target and audit report, then escalate in bounded batches.
10. Hand over failures, manual prerequisites, and post-migration remediation.

The analysis phase does not create target objects and does not download package or library binaries. The apply phase cannot run without a validated plan and explicit approval.

## Supported migration shapes

- **Full lift-and-shift assessment or migration** — every discoverable folder and selected supported entity family.
- **Folder subset** — exact fully-qualified folder paths.
- **Entity subset** — selected entity families such as folders, assets, queues, packages, or processes.
- **Assessment only** — generate the analysis report and stop before staging or applying.
- **Rehearsal or cutover** — the intake records whether the run is a rehearsal or production cutover.

Folder and entity scope are never inferred. A named folder must resolve during discovery or the run stops.

## Entity support and boundaries

The table below separates **assessment/discovery** from **automatic application**. An entity can be fully assessed and reported even when it requires a manual decision or is not eligible for automatic apply.

### Cloud-native Automation Cloud target

| Entity | Automatic application | What migrates | Human intervention or boundary |
|---|---|---|---|
| Folders | Yes | Fully-qualified path, description, feed/provisioning settings, permission model | Folder paths must be resolved and reviewed; parents are created first. |
| Credential stores | Definition only | Name, type, host, and non-secret configuration | Provider secrets never travel. Re-authenticate external vaults before credential assets can resolve. |
| Custom roles | Yes | Custom role definition and permission grants | Built-in/static roles are excluded. Confirm grants succeeded; a failed grant can leave an empty role. |
| Users | Conditional | User mapping when the identity principal resolves | Requires explicit user-import approval and an existing/importable target principal. Unresolved identities are manual review. |
| Machines | Template only | Machine template and slot allocation | Machine keys, license allocation, and robot registrations do not migrate. |
| Robots | No automatic apply | Discovered for assessment | Legacy robots require a Cloud modernization decision, target registration, and machine mapping. |
| Environments | No automatic apply | Discovered for assessment | Legacy environments are replaced by modern folders/machine templates and require manual planning. |
| Assets | Yes, with conditions | Text, integer, Boolean, credential definitions, scope, description, and tags | Credential assets receive a dummy password and require correction. Per-robot asset values are not migrated. |
| Queues | Yes | Queue definition, retries, SLAs, encryption, unique-reference and retention settings | Queue items are a separate family. Validate target behavior and retention policies. |
| Queue items | New-state only | Items whose source state is `New` | In-progress, successful, failed, and historical items do not migrate. Re-runs can duplicate items without unique references. |
| Built-in storage buckets | Yes | Bucket definition and file contents | File transfer is final content work. Verify file counts and required downstream access. |
| External storage buckets | No automatic application | Assessed and reported | Provider credentials/configuration and contents require manual recreation; the engine does not automatically apply external-provider buckets. |
| Packages | Yes, after staging | Missing package binaries and exact versions | Binaries are downloaded locally only after analysis approval. Existing exact ID/version matches are skipped, not overwritten. |
| Tenant-feed libraries | Yes, after staging | Missing tenant-feed library binaries and exact versions | Host-feed libraries are manual review. Feed IDs are not compared across tenants. |
| Processes | Yes | Package binding/version, entry point, arguments, priority, tags, auto-update, visibility, retention | Target package version must exist first. Jobs and execution history do not migrate. |
| Calendars | Yes | Name, timezone, and excluded dates | Verify excluded dates in the target before enabling dependent triggers. |
| Triggers | Yes, supported types | Time, queue, and API trigger definitions, bindings, schedules, thresholds, and enablement | Processes, queues, and calendars must exist first. Review enabled triggers before they can start jobs. |
| Webhooks | Yes | Endpoint URL and event subscriptions | A temporary signing secret is used; rotate it in Cloud before trusting payloads. |
| Feeds | No automatic apply | Configuration is assessed and reported | Feed creation and credentials are environment-specific and require manual setup. |
| Settings | Non-secret keys only | Safe key/value settings | Secret-bearing values, tokens, passwords, connection strings, source URLs, and source feed keys are refused. |
| Bucket files | Built-in buckets only | File content streamed source-to-target | External-provider files require manual review. |

### Direct REST On-Premises target

The direct REST target mode has a deliberately narrower automatic set:

- Folders
- Custom roles
- Machine templates
- Assets
- Queues
- Packages
- Processes
- Calendars

If a plan for a direct REST target contains unsupported families, validation refuses it. Narrow the entity scope rather than forcing unsupported writes.

## Where human intervention is required

Human work is part of the migration design, not an exception hidden after apply. The analysis workbook classifies every source record as **Automatic**, **Hybrid**, **Manual prerequisite**, or **Manual only**.

### Before apply

Clear every blocker and complete every prerequisite shown in the report before staging or applying:

- Confirm the source is reachable and the External Application has the required scopes.
- Confirm the intended target organization, tenant, folder scope, and target population state.
- Confirm target credential-store bindings for credential assets.
- Confirm users and groups exist in the target organization, or explicitly approve supported user import.
- Decide how legacy robots and environments will be modernized.
- Recreate or prepare external storage providers and host-feed libraries.
- Configure feeds and any secret-bearing settings deliberately in the target.
- Resolve ambiguous natural-key matches and duplicate target records.
- Stage every required package and library binary successfully.
- Validate the current plan immediately before apply.

A family containing manual-review records is not mixed into an automatic apply. Because there is no per-record exclusion switch, remove that family from the automated entity scope and handle it separately.

### During and immediately after apply

- Inspect the one-action canary before increasing `--max-actions`.
- Review the per-entity audit report, including failures, skipped items, batch numbers, and exact error reasons.
- Confirm custom role permission grants and folder assignments.
- Verify calendar excluded dates.
- Test processes in the target before cutover.
- Review migrated triggers; enabled triggers may begin firing jobs.
- Reconcile New-state queue-item counts against the source.

### Post-migration remediation

The following values and registrations must be completed manually:

- Set real passwords on credential assets created with dummy passwords.
- Rotate webhook signing secrets.
- Re-authenticate credential stores against their providers.
- Recreate external bucket provider configuration.
- Generate machine keys and register machines/robots.
- Allocate licenses.
- Recreate role assignments and verify folder permissions.
- Set secret-bearing tenant settings and feed credentials.
- Re-establish per-robot asset values.
- Resolve or formally accept every manual-review item.

## What never migrates

The skill does not transfer:

- Real credential passwords, credential-store provider secrets, webhook secrets, external storage credentials, API keys, PATs, or External Application secrets.
- Identity-provider identities and authentication configuration.
- Source entity IDs, creation/modification metadata, read-only server fields, or license state.
- Job history, runtime history, logs, audit history, or robot session state.
- Queue items that are not in `New` state and all queue transaction history.
- Host-feed libraries, feed credentials, built-in/static roles, classic robots, or classic environments as byte-for-byte objects.
- Secret-bearing settings or settings that point back to the source deployment.

## The analysis report

Every migration or assessment produces a decision document before any binary staging or target write. The primary artifacts are:

- `migration-analysis.xlsx` — filterable operator workbook.
- `migration-analysis.html` — offline reviewer report with local review notes.
- `.migration-state/migration-plan.json` — machine-readable plan used by validation and apply.
- Discovery snapshots and staging/apply reports under the configured local artifact area.

### Workbook reading order

1. **Executive Summary** — readiness verdict and blocker count.
2. **Readiness and Blockers** — every blocker and required owner/action.
3. **Disposition by Entity** — source, target, planned, automatic, human, and skipped counts.
4. **Entity Deep Dive** — what migrates, what stays behind, prerequisites, and risks.
5. **Manual Prerequisites** — work that must finish before apply.
6. **Not Migrated by Design** — explicit exclusions and reasons.
7. **Planned Actions** — dependency-ordered operations.
8. **Hybrid Follow-Ups** and **Post-Migration Remediation** — work that remains after apply.
9. **Skipped** — every skipped record and its reason.
10. **Apply Sequence** — dependency order and gate checkpoints.
11. **Approval Runbook** — approvals still outstanding.

### Sample report excerpt

The following is synthetic example data for illustration only; it is not from a real tenant:

```text
Migration Analysis — FinanceOps Rehearsal
Source: On-Premises / FinanceOps      Target: Automation Cloud / FinanceOps-Cloud
Verdict: BLOCKED — 3 blockers, 5 manual prerequisites

Entity family       Source  Target  Planned  Automatic  Human  Skipped  Result
folders                  8       2         6          6       0        2  Ready
credential_stores       3       0         3          0       3        0  Manual prerequisite
users                   42      37         5          2       3        37  Review identity mapping
assets                 126      80        46         38       8         0  Ready with remediation
queues                  12       8         4          4       0         8  Ready
packages                18      14         4          4       0        14  Stage 4 binaries
processes               21      14         4          4       0         3  Depends on packages
triggers                 9       6         3          3       0         6  Review enablement

Blockers
1. Target credential-store binding is not confirmed for credential assets.
2. Two source folders do not resolve to the requested target scope.
3. One required package version is missing from the target and requires binary staging after analysis approval, followed by validation.

Manual prerequisites
- Re-authenticate the `FinanceVault` credential store in the target.
- Confirm three unresolved user principals in the target organization.
- Decide how two legacy robots will be replaced by machine templates.
- Configure the external storage provider for the `Invoices` bucket.
- Review enabled triggers before cutover.

Planned sequence
folders → credential stores → roles/users → machines → assets/queues/buckets
→ calendars/webhooks/settings → packages/libraries → processes → triggers
→ bucket files → New-state queue items

Approval state
- Execution plan approval: complete
- Analysis approval: pending blocker clearance
- Binary staging approval: not authorized
- Apply approval: not authorized
```

A real report contains one row per discovered/planned/skipped record, exact reasons for skips and failures, readiness findings, manual work ownership, and the audit trail needed to decide whether to continue.

## Approval gates and safety model

There are three separate gates:

### Gate 1 — Execution plan

Approves discovery and analysis only. It names the source, target, authentication, folder/entity scope, local artifacts, and read-only versus writing phases.

### Gate 2 — Analysis report

Approves staging and validation only. It does not authorize target writes. All blockers and required prerequisites must be cleared, and the operator must review the manual and skipped lists.

### Gate 3 — Apply

Names the target tenant and confirms the canary size, dummy credential behavior, temporary webhook secret behavior, trigger activation risk, and audit-report review. The first write is a one-action canary.

Apply uses sequential bounded batches of 10 actions by default. Each logical item receives one attempt. Queue-item bulk failures are isolated to individual items so neighboring items are not incorrectly marked failed. There is no transactional rollback or bulk undo; inspect between escalation steps.

## Prerequisites

- UiPath Autopilot or UiPath Studio with the Autopilot skills directory available.
- Python 3.10 or newer on `PATH`.
- UiPath `uip` CLI on `PATH`, authenticated to the target organization and tenant.
- Source access and an External Application for direct REST discovery of On-Premises or Automation Suite.
- Permissions to read the selected source entities and create the selected target entities.

The engine uses only the Python standard library. No `pip install` step is required.

## Install for UiPath Autopilot

From the repository root on Windows PowerShell:

```powershell
.\install\install.ps1
```

From macOS/Linux/Git Bash:

```bash
./install/install.sh
```

The installers copy the skill to:

```text
~/.autopilot/skills/uipath-cloud-migration
```

Use `-Force` or `--force` only when intentionally replacing an existing installation. Restart UiPath Studio/Autopilot after installation so the skill catalog refreshes.

## Use it

Ask Autopilot to perform an assessment or migration:

> Migrate my On-Prem Orchestrator tenant to Automation Cloud. Ask the complete intake first, show me the execution plan, produce the read-only Excel and HTML analysis reports, and wait for separate approval before staging or applying.

For an assessment only:

> Assess the FinanceOps tenant for migration to Automation Cloud. Produce the analysis workbook and HTML report, identify every blocker and human prerequisite, and stop before staging or applying.

The skill reads the bundled documentation under `uipath-cloud-migration/docs/` before invoking the engine. Do not bypass its intake, analysis, validation, canary, or approval gates.

## Authentication and secret handling

- On-Premises and Automation Suite sources use direct REST with an External Application.
- Automation Cloud sources for supported tenant-to-tenant copies use the existing authenticated `uip` CLI session; direct-REST External Application credentials are not used for that source mode.
- Automation Cloud targets use the authenticated `uip` CLI session.
- The configuration stores environment-variable **names**, never client IDs, client secrets, passwords, tokens, or webhook secrets.
- Never paste a real secret into chat or commit it to the repository.
- Source credential variables are created in the operator's own terminal, for example `UIP_ONPREM_CLIENT_ID` and `UIP_ONPREM_CLIENT_SECRET`.
- Generated inventories, plans, reports, staged binaries, and apply results remain operator-local and may contain sensitive tenant information.

## Package layout

```text
uipath-cloud-migration/
├── SKILL.md                 # Autopilot policy and approval gates
├── agents/openai.yaml       # Agent display metadata
├── docs/                    # Engine procedure and current CLI contract
├── references/              # Intake, entity, safety, and boundary policy
└── scripts/                 # Migration engine and smoke test
install/                     # Autopilot installers
assets/                      # Optional intake form
tests/tasks/                 # Smoke and fixture-analysis evaluation tasks
```

## Local artifacts and Git safety

Migration configuration, snapshots, plans, reports, staged `.nupkg` files, and apply results are operator-local and may contain tenant inventories. Do not commit or share them unsanitized. The repository ignore rules cover common variants, including:

```text
migration.local.json
.local/
.migration-state/
*discovery*.json
*migration-plan*.json
*analysis*.xlsx
*analysis*.html
*apply*.json
*apply*.xlsx
DownloadedPackages/
*.nupkg
__pycache__/
.env
*.log
```

## Documentation map

- [`uipath-cloud-migration/SKILL.md`](uipath-cloud-migration/SKILL.md) — active policy and approval gates.
- [`uipath-cloud-migration/references/intake-questionnaire-guide.md`](uipath-cloud-migration/references/intake-questionnaire-guide.md) — mandatory intake and stop conditions.
- [`uipath-cloud-migration/references/entity-policy-guide.md`](uipath-cloud-migration/references/entity-policy-guide.md) — dependency order, matching, and apply support.
- [`uipath-cloud-migration/references/migration-boundaries-guide.md`](uipath-cloud-migration/references/migration-boundaries-guide.md) — exactness limits and post-migration checklist.
- [`uipath-cloud-migration/references/safety-and-approval-guide.md`](uipath-cloud-migration/references/safety-and-approval-guide.md) — secret invariants, canary policy, and approval gates.
- [`uipath-cloud-migration/docs/references/analysis-workflow.md`](uipath-cloud-migration/docs/references/analysis-workflow.md) — read-only discovery and report workflow.
- [`uipath-cloud-migration/docs/references/apply-workflow.md`](uipath-cloud-migration/docs/references/apply-workflow.md) — staging, validation, canary, escalation, and remediation.
- [`uipath-cloud-migration/docs/references/cli-reference.md`](uipath-cloud-migration/docs/references/cli-reference.md) — current engine commands and artifact contract.

## Evaluation tasks

The task definitions under `tests/tasks/uipath-cloud-migration/` cover:

- Fixed-intake enforcement before migration commands.
- Fixture-based assessment analysis without network access or apply.

No live tenant, credential, report, snapshot, package, or apply artifact belongs in this repository.
