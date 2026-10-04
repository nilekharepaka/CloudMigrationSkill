# Apply Workflow

Everything here happens **after** the operator has approved the analysis workbook. Steps 3 onward create objects in the target tenant.

`$MIG` is the installed engine path — see [cli-reference.md](cli-reference.md).

---

## 1. Stage binaries

Only when packages or tenant-feed libraries are in scope.

```powershell
python $MIG stage-packages --config migration.local.json --discovery .migration-state\source.json --target-discovery .migration-state\target.json
```

```powershell
python $MIG stage-libraries --config migration.local.json --discovery .migration-state\source.json --target-discovery .migration-state\target.json
```

`--target-discovery` is optional. When omitted, the engine reuses `.migration-state\target.json` if present; otherwise it performs a narrow live discovery of only the relevant target binary family. Exact normalized package/library ID plus version matches are reported as `already_in_target` and are not downloaded. Feed IDs are not compared across tenants. Different versions, incomplete identities, and ambiguous duplicate target keys remain eligible for staging. A target discovery error fails closed.

Read both reports for download failures and review `already_in_target_count`, `downloaded_count`, and `target_match_source`. A package or library that failed to download will fail at apply — fix it now.

## 2. Validate

```powershell
python $MIG validate --config migration.local.json --plan .migration-state\migration-plan.json
```

Validation runs against the live target. Re-run it immediately before apply, not once at the start of the week.

Common failures and their meaning:

| Failure | Meaning | Fix |
|---|---|---|
| `Unexpected plan version` | Plan built by a different engine version | Regenerate with `analyze` |
| Plan contains manual-review actions | Manual entities mixed with auto-apply | Narrow `entities`, regenerate |
| Plan exceeds the direct REST apply set | On-Prem target, unsupported entities | Narrow `entities` for that target |
| Missing staged binary | Packages not staged, or wrong staging folder | Return to step 1 |

## 3. Apply approval

Ask as its own question. Name the target tenant. Confirm the canary size, the dummy credential password behavior, the temporary webhook secret behavior, and that migrated triggers may begin firing jobs.

Every apply is executed in sequential bounded batches. The general batch size is `config.apply_batch_size` (default `10`); queue items additionally use `queue_item_batch_size` (default `10`) for the bulk transport. Each logical item receives exactly one attempt. If it fails, the exact CLI/API or exception reason is recorded immediately; only that item is marked failed and processing continues with the next item and entity family when continuation is enabled. If a queue bulk request fails, the engine makes one isolated attempt per item and records only items that fail that isolated call.

## 4. Canary

```powershell
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --max-actions 1 --yes
```

Then **inspect the target tenant** — in the Orchestrator UI, or with read commands:

```bash
uip or folders list --output json
```

The inspection is the point of the canary, not the small number.

## 5. Escalate

```powershell
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --max-actions 25 --yes
```

Inspect again. `--max-actions` re-walks the plan from the start; objects already created are skipped as existing, so escalating is a resume, not a duplicate run.

## 6. Full apply

```powershell
python $MIG apply --config migration.local.json --plan .migration-state\migration-plan.json --yes
```

## 7. Read the results

```powershell
python -c "import json;d=json.load(open('.migration-state/apply-results.json'));print(len(d.get('commands',[])),'actions');print(json.dumps(d.get('manual_remediation',[]),indent=2))"
```

Continue-on-error means per-item failures are reported, not raised. Each result carries `batch_number`, `attempts`, and `max_attempts`; queue-item results also identify the bulk batch and whether individual fallback was used. The human-readable audit workbook reports one row per logical item, so a failed queue item does not make its neighboring items appear failed.

## 8. Remediation handover

Deliver the plan's manual-remediation entries plus the standing post-migration checklist:

- Real passwords on every credential asset created with a dummy value.
- Webhook signing secrets rotated in Cloud.
- Calendar excluded dates verified in the target — the engine migrates them through the API, and dependent time triggers must not be enabled until the target calendar is confirmed.
- Custom role permission grants confirmed (check the results file for failures at the `post-create` stage).
- Secret-bearing tenant settings set deliberately in the target.
- Credential stores recreated; credential assets repointed.
- Machine keys generated; robots and machines registered; licenses allocated.
- Role assignments recreated.
- Migrated triggers reviewed and enabled deliberately.
- Storage bucket contents re-uploaded where automations need them.
- Every manual-review entity resolved or formally accepted as out of scope.

---

## Recovery

There is no bulk undo. Orchestrator creates are not transactional.

**A canary created the wrong thing.** Delete the small number of objects manually, fix the config or scope, regenerate the plan, re-validate, re-canary.

**Full apply failed partway.** Read the results file for what landed. Fix the cause, re-run apply against the same plan — existing objects are skipped. Do not delete target objects to force a clean run unless the operator explicitly asks.

**Applied to the wrong tenant.** Stop immediately. Do not attempt an automated cleanup. Enumerate what was created from the results file, and hand the list to the operator for a deliberate decision.
