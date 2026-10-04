# Invoking the Migration Skill

## When it fires on its own

Ask for a migration in ordinary terms and the skill loads:

```text
Migrate my On-Prem Orchestrator tenant to Automation Cloud.
```

```text
We're moving off Automation Suite. Do a pre-migration assessment of the FinanceOps tenant.
```

```text
Lift and shift folders, assets, queues, packages, processes and triggers from OnPremProd to CloudProd.
```

## Explicit invocation

```text
/uipath-cloud-migration Assess my On-Prem tenant and produce the analysis report. Don't apply anything yet.
```

## A good opening prompt

The skill will ask for intake details either way, but supplying them up front saves a round trip:

```text
Migrate On-Prem Orchestrator to Automation Cloud.

Shape: targeted — specific folders, production cutover, single run.
Source: Automation Suite, tenant OnPremProd, https://as.contoso.com, identity at
https://as.contoso.com/identity, direct REST with an External App that already
exists and holds the OR.* scopes.
Target: Automation Cloud, org contoso, tenant CloudProd, already logged in.
Scope: folders Finance/AP and Finance/AR; entities folders, assets, queues,
packages, processes, triggers.

Produce the analysis report first. Ask me before staging packages and again
before applying anything.
```

## What to expect, in order

The flow is fixed. It does not change with the request, the shape, or how certain you are.

1. **The full intake questionnaire, every time** — three blocks, in order: source connection first, then scope and shape, then target and policy. You will be asked for the source deployment and URLs, the combined External Application/required-permission gate where direct REST applies, and the migration shape on every run. Authentication is derived from deployment type; no Orchestrator version or manually entered scope list is requested.
2. An execution plan, and a pause for approval.
3. Config creation, then a credential-setting instruction for your shell.
4. Read-only discovery of both tenants.
5. **The analysis report — always, before anything is downloaded or written.** One Excel workbook plus machine state, and a pause for approval. The workbook opens with a readiness verdict and blocker list, followed by one `Entity - <family>` tab per selected entity family; if it says `BLOCKED`, apply cannot run until those issues are cleared.
5a. Whatever the report lists under Manual Prerequisites — things you must create in the target yourself, because they cannot be migrated into it.
6. Package and library staging, if in scope. Machine-readable snapshots and stage reports are kept under `.migration-state` by default.
7. Plan validation.
8. A separate apply approval request, naming the target tenant.
9. A canary apply, an inspection pause, then escalation. Each run produces `apply-report.xlsx` with one `Apply - <family>` tab per selected entity family.
10. Full apply.
11. A post-migration remediation list.

The skill will not skip steps 1, 2, 5 or 8 on request — those gates are the point of the tool. The analysis report in particular is not optional: the engine refuses to apply a plan that was not produced together with a report, so asking to "just migrate it" cannot work even in principle.

## Common variations

**Full lift-and-shift.** Say so, and expect it read back to you as "every discoverable folder and every supported entity family" before anything runs. The tool refuses a config that claims lift-and-shift while quietly narrowing folder or entity scope.

**Assessment only.** Say so up front. The run stops after the analysis workbook, and nothing is downloaded or created. This is the fastest way to get a 360° readiness view of a tenant: what migrates cleanly, what needs a follow-up, what you must build by hand first, and what has no migration path at all.

```text
I only want an assessment. Produce the analysis workbook and stop.
```

**Rehearsal against a sandbox tenant.** Point the target at a scratch tenant first. Keep the same canary discipline — a sandbox rehearsal that skips the canary teaches you nothing about the real run.

**Narrow scope, repeated.** Migrating one entity family at a time is safer than one big run, and the dependency order still applies across runs: folders before folder-scoped resources, packages before processes, processes before triggers.

**Reusing snapshots.** For a large tenant, discovery can dominate the runtime. Ask for snapshots to be captured once and reused for subsequent planning.

**On-Prem to On-Prem.** Supported with a narrower auto-apply set. Say the target is On-Prem so the skill configures the direct REST target path and keeps the entity scope inside what it supports.

## What to have ready before you start

- The `uip` CLI installed and on `PATH`.
- Python 3.10 or newer.
- A source External Application with Orchestrator scopes covering the entities in scope.
- For direct REST sources, both credential environment variables created before `probe` or source `discover`: `UIP_ONPREM_CLIENT_ID` and `UIP_ONPREM_CLIENT_SECRET` (or the configured names).
- A `uip` login to the target organization and tenant.
- The exact fully-qualified folder paths you intend to migrate, or a decision to migrate all of them.

Create the two variables yourself in PowerShell; use the real values only in your own terminal, never in chat or a migration file:

```powershell
setx UIP_ONPREM_CLIENT_ID "<source external app id>"
setx UIP_ONPREM_CLIENT_SECRET "<source external app secret>"
```

After `setx`, open a new PowerShell before running the probe or discovery. `setx` updates persistent user environment state for processes started afterward; it does not update the shell that executed it. If an assistant runs the migration commands, the persistent variables are required because each command may run in a fresh process.

## What you own afterwards

Apply finishing is not the migration finishing. You will be handed a remediation list. At minimum it will include real passwords for credential assets, webhook secret rotation, machine keys and robot registration, role assignments, and a decision on enabling migrated triggers.
