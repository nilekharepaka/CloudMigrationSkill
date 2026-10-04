---
name: uipath-cloud-migration
description: "Migrate UiPath Orchestrator tenants from On-Premises or Automation Suite to Automation Cloud, and perform supported tenant-to-tenant copies or pre-migration assessments."
when_to_use: "Trigger for requests to migrate an Orchestrator tenant to Cloud, perform a lift-and-shift, move folders/assets/queues/packages/processes/triggers between tenants, create a migration analysis, validate a migration plan, or run a canary/full migration. Do not use for single-resource CRUD, one-project deployment, or job-failure diagnosis."
---

# UiPath Cloud Migration

Use this skill to assess and migrate supported UiPath Orchestrator entities from an On-Premises or Automation Suite source tenant to an Automation Cloud target tenant. The implementation is review-first and approval-gated: collect the full intake, run read-only discovery and analysis, obtain approval, stage binaries only after approval, validate, run a canary, inspect it, and only then run a larger or full apply.

The migration engine and its exact syntax are bundled under `{SKILL_DIR}/docs/`. Read the relevant bundled documentation before invoking the engine. The policy references under `{SKILL_DIR}/references/` define safety, scope, entity behavior, and migration boundaries.

## Autopilot execution rules

1. **Use the active Autopilot skill directory.** In Studio, this skill is loaded from the user-level Autopilot skills folder. The bundled engine and docs are local operator assets; do not commit or share generated inventories, plans, reports, package binaries, or apply results.
2. **Load `uipath-platform` before touching UiPath Cloud or Orchestrator.** Prefer the supported `uip` command families. Use direct Orchestrator REST only for source extraction from On-Premises/Automation Suite and for target fields that the supported command surface cannot express.
3. **Read the bundled migration docs before the first engine call in a session.** Use the engine documentation for the current subcommand names, flags, configuration schema, plan schema, artifact names, and troubleshooting. Never infer those details from memory or from an older skill version.
4. **Do not run a migration command until the fixed intake is complete.** Ask every source, scope, target, and policy question in the intake reference. Never reuse a prior answer silently and never infer a tenant, URL, folder scope, entity scope, or credential-store mapping.
5. **Create both source credential environment variables before direct REST access.** Ask only for the variable names, never the values. Before `probe` or source `discover`, the operator must create both the client-ID and client-secret variables in their own shell. On Windows, the persistent setup is:
   ```powershell
   setx UIP_ONPREM_CLIENT_ID "<source external app id>"
   setx UIP_ONPREM_CLIENT_SECRET "<source external app secret>"
   ```
   Open a new PowerShell after `setx`; new processes inherit the variables, while the shell that ran `setx` does not. Never paste commands containing real values into chat, and never write the values to config, plans, reports, or snapshots. The target uses the authenticated `uip` session; do not create a second target credential path.
6. **Check reachability and credentials without exposing values.** On-Premises and Automation Suite sources use direct REST with an External Application. Do not attempt a `uip` login against an on-prem Identity Server. A Cloud source may use an existing `uip` session.
7. **Require the execution-plan gate before configuration or discovery.** Present source and target, authentication, resolved folder/entity scope, local artifacts, ordered phases, and which phases are read-only or writing. Wait for an explicit approval.
8. **Analysis is mandatory and read-only.** The analysis must produce the human-readable report and machine-readable migration definition before any package/library binary is downloaded and before any target object is created. A source-only assessment stops after this report.
9. **Readiness blockers are hard stops.** Do not stage or apply while blockers or unfinished manual prerequisites remain. Manual-review records are reported out of band; do not force them through automatic apply.
10. **Require a separate apply approval.** Approval of the execution plan authorizes discovery and analysis. Approval of the analysis authorizes staging and validation. Neither authorizes target writes. Apply approval must name the target tenant and confirm the canary, dummy credential behavior, temporary webhook secret behavior, and trigger activation risk.
11. **Validate immediately before applying.** Apply only a validated plan generated from the approved discovery and analysis. Never hand-edit the plan. If the target has drifted, regenerate and revalidate.
12. **Canary first, inspection second.** The first write against a new target is a one-action canary. Inspect the target and the per-entity audit report before escalating. Every apply, including a canary, must produce a human-readable audit report stating successes, failures, reasons, and what was not reached.
13. **Do not promise an exact clone.** Source IDs, audit metadata, job/runtime history, logs, robot session state, license state, identity-provider identities, real credential passwords, credential-store/provider secrets, external storage-provider secrets, and other server-owned fields do not migrate. State these limits before analysis approval.
14. **Use dependency-safe phases.** Plan and apply configuration entities first: folders; credential stores; roles; users; machines; legacy robots; legacy environments; assets; queues; storage buckets; calendars; webhooks; feeds; settings. Reconcile packages and libraries late in the binary phase, then apply package-dependent processes and triggers, then transfer bucket files, with queue items last. Packages still precede processes, and processes, queues, and calendars precede triggers.
15. **Treat generated artifacts as sensitive operator-local data.** Discovery snapshots and analysis reports contain tenant inventories. Keep them local and do not paste them into chat or shared channels unless sanitized.
16. **Finish with remediation.** Review the audit report and hand over outstanding work: real credential passwords, webhook-secret rotation, provider-store reauthentication, calendar excluded dates, machine keys and robot registration, role assignments, feed/settings review, trigger review, and reconciliation of New-state queue items.
17. **Apply in bounded batches with immediate failure reporting.** Every apply uses sequential bounded batches of 10 actions by default. Each logical item receives exactly one attempt; if it fails, the exact CLI/API or exception reason is recorded and processing continues with later items and entity families when continuation is enabled. Queue bulk failures make one isolated attempt per item so neighboring queue items are not marked failed together; queue-item bulk calls are also capped at 10 by default.

## Fixed intake

Read `{SKILL_DIR}/references/intake-questionnaire-guide.md` in full before asking intake questions. Ask the three blocks in order:

- **Source connection:** deployment type, Orchestrator and Identity URLs where applicable, source tenant, and the combined External Application/required-permission readiness gate. Authentication is derived automatically from deployment type, and credential-variable names are collected only for direct REST. Reachability and granted permissions are checked automatically.
- **Migration shape and scope:** full lift-and-shift, folders, entity families, assessment-only, queue items, bucket contents, rehearsal/cutover, exact folder paths, and exact entity families.
- **Target and policy:** target existence/type, target organization and tenant, population state, identity-principal status, user import, authority, credential-store binding, and canary size.

Read all answers back before proceeding. Any unanswered or ambiguous item is a stop condition.

## Four stages

### Stage 1 — Intake and execution plan

No source or target migration calls occur until the intake is complete and the execution plan is explicitly approved. Configuration creation is local-only and must never contain secret values.

### Stage 2 — Read-only analysis

Confirm target authentication, discover both sides, resolve folder scope from actual fully-qualified folder names, compare natural keys, classify every source record, and generate the report and migration definition. Present:

- readiness verdict and every blocker;
- source/target counts and conflicts;
- automatic, hybrid, manual-prerequisite, and manual-only dispositions;
- planned actions in dependency order;
- skipped items and reasons;
- manual prerequisites and post-migration remediation;
- risk assessment and exactness boundaries.

### Stage 3 — Analysis confirmation

Walk the operator through the report. Do not proceed while a blocker or required prerequisite remains. Explicitly confirm that this approval covers staging and validation only.

### Stage 4 — Controlled migration

After analysis approval, stage in-scope package/library binaries, validate the live target, ask for separate apply approval, run the canary, inspect the target and audit report, then escalate or run the full apply only after explicit approval. Report all successes, failures, skips, and remediation items.

## Entity boundary

For a Cloud-native target, automatic application can cover folders, custom roles, users when explicitly enabled and resolvable, machine templates, assets, queues, built-in storage buckets, packages, tenant-feed libraries, processes, calendars, supported triggers, webhooks, non-secret settings, built-in bucket files, and New-state queue items. Credential stores are definitions only and provider secrets do not travel.

For a direct REST On-Premises target, the automatic set is narrower: folders, custom roles, machine templates, assets, queues, packages, processes, and calendars.

Manual review is required for legacy robots and environments, feeds, host-feed libraries, external storage providers, built-in/static roles, unconfirmed identity principals, secret-bearing settings, and any entity the target API cannot safely create. Credential assets use a dummy password and require correction. Webhooks use a temporary secret and require rotation. Calendar excluded dates are migrated through the API but must be verified in the target before dependent triggers are enabled; provider configuration, machine/robot keys, role assignments, and other listed remediation are not silently treated as complete.

## References

- `{SKILL_DIR}/references/intake-questionnaire-guide.md` — mandatory intake and stop conditions
- `{SKILL_DIR}/references/entity-policy-guide.md` — dependency order, matching, scope, and apply boundary
- `{SKILL_DIR}/references/safety-and-approval-guide.md` — approval gates, canary policy, and secret invariants
- `{SKILL_DIR}/references/migration-boundaries-guide.md` — non-migrated data and post-migration checklist
- `{SKILL_DIR}/docs/SKILL.md` — ordered engine procedure
- `{SKILL_DIR}/docs/USAGE.md` — invocation guidance
- `{SKILL_DIR}/docs/references/cli-reference.md` — current engine syntax and artifact contract
- `{SKILL_DIR}/docs/references/analysis-workflow.md` — read-only discovery and analysis recipe
- `{SKILL_DIR}/docs/references/apply-workflow.md` — staging, validation, canary, full apply, and recovery
