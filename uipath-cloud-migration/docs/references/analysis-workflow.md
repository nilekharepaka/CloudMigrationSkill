# Analysis Workflow (Read-Only)

Everything here is read-only against both tenants. Nothing is downloaded and nothing is created. This is the workflow to run for a pre-migration assessment.

`$MIG` is the installed engine path — see [cli-reference.md](cli-reference.md).

---

## 1. Config

```powershell
python $MIG init-config --out migration.local.json
```

The operator sets these themselves, directly in their own terminal — never by pasting a command containing the real value into this conversation; a value typed into the conversation is compromised and must be rotated regardless of what happens next.

A human typing every command in one continuous terminal:

```powershell
$env:UIP_ONPREM_CLIENT_ID = "<source external app id>"
$env:UIP_ONPREM_CLIENT_SECRET = "<source external app secret>"
```

An assistant running the remaining commands on the operator's behalf needs a persistent variable instead, since each command it runs is typically a fresh process:

```powershell
setx UIP_ONPREM_CLIENT_ID "<source external app id>"
setx UIP_ONPREM_CLIENT_SECRET "<source external app secret>"
```

## 2. Confirm access

```bash
uip login status --output json
```

```bash
uip login tenant set "<target-cloud-tenant>" --output json
```

## 3. Verify folder scope before anything else

The single most common assessment error is a folder scope that matches nothing. Discover folders first with the scope left open:

```powershell
python $MIG discover --config migration.local.json --side source
```

Read the fully-qualified folder names out of the snapshot:

```powershell
python -c "import json;d=json.load(open('.migration-state/source.json'));print('\n'.join(sorted(r.get('FullyQualifiedName') or r.get('Name','') for r in d['entities']['folders'])))"
```

Then narrow `folder_paths` in the config to the exact strings that came back.

## 4. Target snapshot

```powershell
python $MIG discover --config migration.local.json --side target
```

## 5. Analysis workbook and plan

```powershell
python $MIG analyze --config migration.local.json --source-discovery .migration-state\source.json --target-discovery .migration-state\target.json
```

Without pre-captured snapshots, `analyze` discovers both sides itself:

```powershell
python $MIG analyze --config migration.local.json
```

`analyze` writes the visible `migration-analysis.xlsx` workbook beside the config, machine-readable plan state under `.migration-state`, and a companion HTML report (same base filename, `.html`) — the baseline reviewer-oriented report for migration sizing. It uses viewer-safe anchor navigation with six always-visible sections: **Review workspace**, **Executive summary**, **Effort & blockers**, **Entity inventory**, **Human work details**, and **Migration plan**. The review workspace is local-only and records review status, owner, dates, comments, and row-level notes without changing the migration plan or calling UiPath. Open the report first to review the headline volume and readiness findings; the entity inventory reconciles source, target, planned, automatic, hybrid, prerequisite, manual-only, and skipped counts for every family. The Human work details section names every non-automatic action with its identity, reason, required action, timing, and owner/hours placeholder. The headline no-human-intervention percentage is automatic actions divided by planned actions (skipped records are excluded because they already exist in the target). The report does not invent effort hours; use the placeholders to size customer work.

## 6. Read the workbook in this order

Top to bottom is the intended reading order — the sheets are arranged as a decision path, not a data dump.

| # | Sheet | What to check |
|---|---|---|
| 1 | `Executive Summary` | The readiness verdict. `BLOCKED` means apply cannot run yet, and the blocker count tells you how much stands in the way. |
| 2 | `Readiness and Blockers` | Every `Blocker` row must be cleared. Every `Action required` row must have an owner. Read the `Impact if ignored` column before dismissing anything. |
| 3 | `Disposition by Entity` | The 360° matrix. Check `Source` = `Planned` + `Skipped` per row; a mismatch means records fell out silently. The `Verdict` column tells you which families must be excluded from the automated run. |
| 4 | `Entity Deep Dive` | Per entity: what migrates, what stays behind, what must exist first, and the principal risk. This is the sheet that prevents wrong assumptions about fidelity. |
| 5 | `Manual Prerequisites` | Work that must be finished **before** apply. Anything left here will fail the run. |
| 6 | `Not Migrated by Design` | Confirm nothing the operator expected is silently excluded. |
| 7 | `Planned Actions` | The actual work, in dependency order, with a disposition on every row. |
| 8 | `Hybrid Follow-Ups` and `Post-Migration Remediation` | The work that outlives apply. Size it now, not after cutover. |
| 9 | `Skipped` | Every record not planned, with its reason. |
| 10 | `Apply Sequence` | The order and the gate before each step. |
| 11 | `Approval Runbook` | The gates still outstanding. |

A source count of zero for a family the operator expected to be populated is a folder-scope or External-App-scope problem, not an empty tenant. The readiness sheet raises it, but confirm it out loud.

## 7. Report back

An assessment deliverable should state:

- The readiness verdict, and every blocker with its resolution.
- What must be created manually in the target **before** apply can run at all.
- The split across the four dispositions: fully automatic, hybrid, manual prerequisite, manual only.
- Per entity, what migrates and what stays behind.
- What is not migrating at all, and why — history, non-New queue items, real secrets, machine keys.
- Which families must be dropped from the automated run because they mix manual and automatic records.
- The apply sequence, where the canary stops, and the expected duration drivers (queue item count and total bucket bytes).

Stop here for an assessment. Staging, validation, and apply are the separate workflow in [apply-workflow.md](apply-workflow.md).

---

## Snapshot hygiene

- Snapshots contain a full tenant inventory. Operator-local, never committed.
- A snapshot is a point-in-time read. Re-discover before planning if either tenant may have changed.
- For repeated rehearsals, reusing snapshots keeps runs fast and keeps load off a production source.
