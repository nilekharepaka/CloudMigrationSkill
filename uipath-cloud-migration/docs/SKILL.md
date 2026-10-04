# Migration Engine — Internal Procedure

Ordered procedure with concrete commands. Policy that governs each step lives in the `uipath-cloud-migration` skill; this file is the execution detail.

`$MIG` is the installed engine path — see [references/cli-reference.md](references/cli-reference.md) § Installed engine path.

---

## The four fixed stages

MIG-1..4 are stage 1 (intake), MIG-5..6 stage 2 (analysis), MIG-7..7a stage 3 (confirmation), MIG-8..12 stage 4 (migration). The order never varies, and stages 1-3 are never skipped on any request.

## MIG-1 — Fixed intake

Ask the **entire** questionnaire in the skill's `references/intake-questionnaire-guide.md` — all three blocks, every run, regardless of what the operator supplied in their opening message. Run no command in this step.

## MIG-2 — Execution plan review

Present source, target, auth mode, folder scope, entity scope, artifact list, and the ordered command sequence. Mark each phase read-only or writing. Wait for approval.

## MIG-3 — Config init

```powershell
python $MIG init-config --out migration.local.json
```

Skip when a valid config already exists. Add `--overwrite` only when the operator wants the existing config replaced.

Then the operator sets the source External App credentials themselves, directly in their own terminal — never by pasting a command containing the real value into this conversation. A value that reaches this conversation is compromised and must be rotated regardless of what happens next.

A human typing every command in one continuous terminal can use a session-scoped variable:

```powershell
$env:UIP_ONPREM_CLIENT_ID = "<source external app id>"
$env:UIP_ONPREM_CLIENT_SECRET = "<source external app secret>"
```

When an assistant is running the remaining commands on the operator's behalf, use a persistent variable instead — each command it runs is typically a fresh process, so a session-scoped variable set in the operator's terminal is invisible to it:

```powershell
setx UIP_ONPREM_CLIENT_ID "<source external app id>"
setx UIP_ONPREM_CLIENT_SECRET "<source external app secret>"
```

## MIG-4 — Access check

```bash
uip login status --output json
```

```bash
uip login tenant set "<target-cloud-tenant>" --output json
```

For a `direct_rest` source, confirm reachability with a narrow discovery run rather than a full one — set `entities` to `["folders"]` temporarily, or read the folder list from the first discovery snapshot.

## MIG-5 — Discovery

```powershell
python $MIG discover --config migration.local.json --side source
```

```powershell
python $MIG discover --config migration.local.json --side target
```

Read the folder counts out of the snapshots before continuing. Zero folder-scoped records means the folder scope is wrong — fix it here, not after the workbook is built.

`analyze` performs its own discovery, so MIG-5 is optional for a small tenant. Run it explicitly when the tenant is large, when rehearsing offline, or when the folder scope is unverified.

## MIG-6 — Analysis and plan generation

```powershell
python $MIG analyze --config migration.local.json
```

Reusing snapshots from MIG-5:

```powershell
python $MIG analyze --config migration.local.json --source-discovery .migration-state\source.json --target-discovery .migration-state\target.json
```

## MIG-7 — Analysis review

Hand over `migration-analysis.xlsx` and walk the operator through it in sheet order:

1. `Executive Summary` — the readiness verdict and the disposition split.
2. `Readiness and Blockers` — every `Blocker` row must be cleared before MIG-8. Do not proceed while one stands.
3. `Disposition by Entity` — which families are fully automatic, which must be excluded from the automated run.
4. `Entity Deep Dive` — per entity, what migrates and what stays behind.
5. `Manual Prerequisites` — hand this to the operator as work to complete before apply.
6. `Not Migrated by Design` — confirm nothing expected is silently excluded.

The same findings are in the plan file under `readiness`, and every action carries a `disposition`.

Wait for confirmation. This approves staging and validation only.

## MIG-7a — Manual prerequisites

The operator completes everything on the `Manual Prerequisites` sheet in the target: identity principals, external-provider buckets, target feeds, and any credential store the config needs to reference. Apply will fail on anything left undone.

## MIG-8 — Binary staging

Only when packages or tenant-feed libraries are in scope.

```powershell
python $MIG stage-packages --config migration.local.json --discovery .migration-state\source.json
```

```powershell
python $MIG stage-libraries --config migration.local.json --discovery .migration-state\source.json
```

Omit `--discovery` to re-discover the source. Use `--target-discovery <path>` to provide an explicit target inventory for binary matching; when omitted, the engine reuses `.migration-state\target.json` when present or performs a narrow live discovery of only the relevant target binary family. Exact normalized package/library ID plus version matches are reported as `already_in_target` and never downloaded. Ambiguous or incomplete target identities fail open to staging, while target discovery errors fail closed. Read the stage reports for download failures and target-match counts before validating.

## MIG-9 — Validation

```powershell
python $MIG validate --config migration.local.json --plan .migration-state\migration-plan.json
```

Resolve every error before apply. A validation failure means the target drifted or the plan is stale — regenerate rather than patch.

## MIG-10 — Canary apply

Ask for apply approval as its own question, naming the target tenant. Then:

```powershell
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --max-actions 1 --yes
```

Inspect the target tenant. Then escalate:

```powershell
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --max-actions 25 --yes
```

**The apply gate.** Apply refuses any plan that was not generated by `analyze`. If you produced the plan with the bare `plan` subcommand, apply rejects it — regenerate with `analyze` so the operator has a report to approve.

## MIG-11 — Full apply

Only after the canary is verified in the target.

```powershell
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --yes
```

Read `apply-results.json` for per-item failures. Apply always uses sequential bounded batches (`apply_batch_size`, default `10`) and exactly one attempt per logical item. The exact CLI/API or exception reason is recorded immediately; with continue-on-error enabled, the next item and entity family continue. Queue items use bulk transport capped at 10 items by default, then make one isolated attempt per item if the bulk request fails; only items that fail that isolated call are marked failed. Result rows include batch and attempt metadata, and the audit workbook reports one row per logical item.

## MIG-12 — Post-migration remediation

Deliver the outstanding list from the plan's `manual_remediation` entries plus the skill's post-migration checklist. Cover at minimum: real credential asset passwords, webhook secret rotation, credential-store provider re-authentication, calendar excluded-date verification, role permission grants, machine keys and robot registration, manual-review entities, role assignments, secret-bearing settings, and trigger enablement review.

---

## Snapshot Reuse

Any command that discovers accepts a snapshot instead:

| Command | Snapshot flags |
|---|---|
| `analyze` | `--source-discovery`, `--target-discovery` |
| `plan` | `--source-discovery`, `--target-discovery` |
| `stage-packages` | `--discovery`, `--target-discovery` |
| `stage-libraries` | `--discovery`, `--target-discovery` |

Snapshots go stale. Re-discover whenever either tenant may have changed.

---

## Documentation

- [USAGE.md](USAGE.md) — how a caller invokes this skill
- [references/cli-reference.md](references/cli-reference.md) — subcommands, flags, config schema, artifacts, troubleshooting
- [references/analysis-workflow.md](references/analysis-workflow.md) — read-only discovery and analysis recipe
- [references/apply-workflow.md](references/apply-workflow.md) — staging, validation, canary, and full apply recipe
- [references/orchestrator-manager-2.6.2.md](references/orchestrator-manager-2.6.2.md) — behavior and limits inherited from the Orchestrator Manager Lift-and-Shift tool
- [references/entity-mapping.json](references/entity-mapping.json) — machine-readable Lift-and-Shift sheet mapping
- [references/config-example.json](references/config-example.json) — starter config template
