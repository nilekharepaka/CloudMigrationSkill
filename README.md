# UiPath Cloud Migration Skill

A review-first UiPath Autopilot skill for assessing and migrating supported Orchestrator entities from **On-Premises/Automation Suite** to **Automation Cloud**. It also supports source-only assessments and supported tenant-to-tenant copies.

This is a definition migration, not a byte-for-byte clone. Secrets, identities, source IDs, execution history, logs, audit history, robot session state, machine keys, and license state do not transfer.

## Install

### Prerequisites

- UiPath Autopilot or UiPath Studio with Autopilot skills enabled
- Python 3.10+ on `PATH`
- UiPath `uip` CLI on `PATH`, authenticated for Automation Cloud source/target operations
- Source External Application and access for direct REST discovery of On-Premises/Automation Suite

For a direct REST On-Premises target, `uip` authentication is not required; configure the target Orchestrator URL, tenant, and External Application environment-variable names instead. The supported apply set is narrower.

The engine uses only Python's standard library; no `pip install` is required.

### Install to Autopilot

From the repository root:

```powershell
.\install\install.ps1       # Windows PowerShell
```

```bash
./install/install.sh         # macOS, Linux, or Git Bash
```

The skill is installed at:

```text
~/.autopilot/skills/uipath-cloud-migration
```

Use `-Force` or `--force` to replace an existing copy, then restart Studio/Autopilot.

## Supported entities

The table describes the **Automation Cloud target**. “Assess only” means the entity is discovered and reported but is not automatically written.

| Entity | Automatic apply | Important boundary |
|---|---|---|
| Folders | Yes | Matched by fully-qualified path; parents first. |
| Credential stores | Definition only | Provider secrets never migrate; re-authentication is required. |
| Custom roles | Yes | Built-in/static roles excluded; verify permission grants. |
| Users | Conditional | Requires explicit import approval and resolvable target identities. |
| Machines | Templates only | Keys, licenses, and robot registrations do not migrate. |
| Robots, environments | Assess only | Legacy constructs require a Cloud modernization decision. |
| Assets | Yes, with conditions | Credential assets get a dummy password; per-robot values do not migrate. |
| Queues | Yes | Definition migrates; queue items are separate. |
| Queue items | New state only | In-progress, completed, failed, and history do not migrate. |
| Built-in storage buckets/files | Yes | Contents transfer after definitions; verify counts and access. |
| External storage buckets/files | Assess only | Provider configuration, credentials, and contents require manual recreation. |
| Packages, tenant-feed libraries | Yes, after staging | Missing exact ID/version binaries are staged locally after analysis approval. |
| Processes | Yes | Requires the target package version; job history does not migrate. |
| Calendars | Yes | Verify excluded dates before enabling dependent triggers. |
| Time, queue, API triggers | Yes | Review enablement; enabled triggers may start jobs. |
| Webhooks | Yes | Temporary signing secret is used; rotate it in Cloud. |
| Feeds | Assess only | Feed configuration and credentials are environment-specific. |
| Settings | Non-secret keys only | Secret-bearing values and source URLs are refused. |

For a **direct REST On-Premises target**, automatic apply is narrower: folders, custom roles, machine templates, assets, queues, packages, processes, and calendars.

## Where human intervention is required

### Before apply

- Complete the fixed intake: source, URLs, tenant, authentication, folder scope, entity scope, target, credential-store binding, and canary size.
- Clear readiness blockers and manual prerequisites in the analysis report.
- Confirm target users/groups and identity mappings.
- Decide how legacy robots/environments will be modernized.
- Recreate external storage providers, host-feed libraries, feeds, and secret-bearing settings.
- Stage required package/library binaries and validate the plan.

A family containing manual-review records is excluded from automatic apply as a whole; there is no per-record exclusion switch.

### After apply

- Inspect the one-action canary and audit report before escalating.
- Set real credential passwords and rotate webhook secrets.
- Re-authenticate credential stores and external bucket providers.
- Generate machine keys, register robots/machines, allocate licenses, and restore role assignments.
- Test processes, verify calendars, review triggers, and reconcile New-state queue counts.
- Resolve or formally accept every manual-review item.

## Workflow and approval gates

1. **Fixed intake** — no migration command runs before all answers are complete.
2. **Execution-plan approval** — authorizes read-only discovery and analysis only.
3. **Read-only analysis** — produces the workbook, HTML report, and plan; downloads no binaries and creates no target objects.
4. **Analysis approval** — authorizes binary staging and validation only.
5. **Apply approval** — names the target tenant and confirms dummy credential behavior, temporary webhook secret behavior, trigger risk, and canary size.
6. **Canary, inspection, escalation** — first write is one action; inspect the target and audit report between bounded batches.

Apply uses batches of 10 actions by default. Each logical item receives one attempt; failures are recorded with their exact reason. There is no transactional rollback.

## Reports and local artifacts

Analysis produces:

- `migration-analysis.xlsx` — operator workbook with readiness, counts, dispositions, prerequisites, skipped records, planned actions, and approval runbook.
- `migration-analysis.html` — offline reviewer report.
- `.migration-state/migration-plan.json` — plan consumed by validation/apply.
- `apply-report.xlsx` — human-readable per-entity audit report after each apply, including successes, failures, skips, and items not reached.
- Discovery and staging reports — operator-local evidence.

### Small sample

```text
Verdict: BLOCKED — 2 blockers, 4 manual prerequisites

Entity             Source  Target  Planned  Automatic  Human  Skipped
folders                 8       2         6          6       0        2
users                  42      37         5          2       3       37
assets                126      80        46         38       8        0
packages               18      14         4          4       0       14
processes              21      14         4          4       0        3
triggers                9       6         3          3       0        6

Blockers: unresolved credential-store binding; missing target folder scope.
Human work: identity mapping, dummy credential correction, robot modernization,
trigger review, and webhook-secret rotation.
```

The real report contains record-level dispositions, skip/failure reasons, dependency order, and remediation ownership. The sample is illustrative only.

## Authentication and safety

- On-Premises/Automation Suite sources use direct REST with an External Application.
- Automation Cloud sources and targets use the authenticated `uip` CLI session.
- Config files store environment-variable names, never secret values.
- Never paste client secrets, passwords, tokens, or webhook secrets into chat or repository files.
- Generated configs, snapshots, plans, reports, staged `.nupkg` files, and apply results contain tenant data and must remain local/unsanitized only for the operator.

## Use it

Ask Autopilot:

> Assess my On-Prem Orchestrator tenant for Automation Cloud. Collect the complete intake, produce the Excel and HTML analysis reports, explain every blocker and human prerequisite, and stop before staging or applying.

Or:

> Migrate my On-Prem Orchestrator tenant to Automation Cloud. Follow the intake, execution-plan approval, analysis approval, validation, canary, and separate apply approval gates.

## Detailed references

- [`uipath-cloud-migration/SKILL.md`](uipath-cloud-migration/SKILL.md) — active policy and gates
- [`references/intake-questionnaire-guide.md`](uipath-cloud-migration/references/intake-questionnaire-guide.md) — mandatory intake
- [`references/entity-policy-guide.md`](uipath-cloud-migration/references/entity-policy-guide.md) — matching, dependencies, apply support
- [`references/migration-boundaries-guide.md`](uipath-cloud-migration/references/migration-boundaries-guide.md) — non-migrated data and remediation
- [`references/safety-and-approval-guide.md`](uipath-cloud-migration/references/safety-and-approval-guide.md) — secret and canary rules
- [`docs/references/cli-reference.md`](uipath-cloud-migration/docs/references/cli-reference.md) — current commands and artifacts
- [`docs/references/analysis-workflow.md`](uipath-cloud-migration/docs/references/analysis-workflow.md) — analysis procedure
- [`docs/references/apply-workflow.md`](uipath-cloud-migration/docs/references/apply-workflow.md) — staging, validation, and apply procedure

## Package layout and Git safety

```text
uipath-cloud-migration/   # policy, docs, references, engine, tests
install/                  # Autopilot installers
assets/                   # optional intake form
tests/tasks/              # smoke and fixture-analysis tasks
```

Do not commit migration configs, snapshots, reports, staged binaries, apply results, `.env` files, logs, or Python caches. The repository `.gitignore` covers these artifacts.
