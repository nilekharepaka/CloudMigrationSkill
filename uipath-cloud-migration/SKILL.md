---
name: uipath-cloud-migration
description: Migrate UiPath Orchestrator entities from On-Premises or Automation Suite to Automation Cloud. Use for Cloud migration discovery, diffing, planning, validation, and controlled apply of folders, credential stores, users, roles, machines, assets, queues, buckets, packages, libraries, processes, triggers, webhooks, calendars, and legacy assessment entities with uip CLI or direct On-Prem REST.
---

# UiPath Cloud Migration

## Purpose

Use this skill to migrate practical UiPath Orchestrator entities from an On-Premises source tenant to an Automation Cloud target tenant. The helper supports broad discovery and diff planning. Automatic apply remains deliberately conservative and is limited to entity types with implemented safe create/upload commands.

Always load and follow `uipath-platform` before executing commands that touch UiPath Cloud or Orchestrator. Prefer `uip` CLI command families. Use raw REST only after checking `uipath-platform/references/uip-commands.md` and confirming no `uip` command covers the operation.

## Workflow

1. Collect all source and target details before running any migration command.
   - Do not run `init-config`, `discover`, `analyze`, `stage-packages`, `stage-libraries`, `validate`, or `apply` until the operator has provided or confirmed the required source and target details.
   - Ask for source system type, On-Prem/Automation Suite tenant name, Identity URL, Orchestrator URL, source auth mode, External App credential environment variable names, External App scopes, folder scope, entity scope, package staging folder, credential dummy password policy, webhook temporary secret policy, request interval, and batch size.
   - Ask for target Automation Cloud URL/authority when non-default, target organization if relevant, target tenant name, target login status, target folder scope, expected apply mode, and canary batch size.
   - Ask whether the On-Prem source should use `direct_rest` External App auth or `uip` auth. Prefer `direct_rest` for On-Prem and Automation Suite source discovery.
   - Never save client secrets, user keys, passwords, or access tokens in the skill, examples, repository files, migration plan, or discovery snapshots.
   - Use environment variables for External App credentials, for example `UIP_ONPREM_CLIENT_ID` and `UIP_ONPREM_CLIENT_SECRET`.
2. Present the execution plan and wait for operator approval.
   - Summarize source and target, auth method, selected folder/entity scope, output files, and the command sequence.
   - State explicitly that `analyze` is read-only, package/library staging downloads files only after approval, and `apply` creates/updates target Cloud resources only after a second explicit approval.
   - Include the planned commands for `init-config` if needed, `analyze`, review checkpoint, `stage-packages`/`stage-libraries` if needed, `validate`, canary `apply`, and full `apply`.
   - Do not proceed beyond the plan until the operator confirms.
3. Create a local, unshared migration config.
   - Prefer `scripts/uip_cloud_migration.py init-config --out migration.local.json`.
   - The interactive setup writes only tenant names, URLs, folder scope, entity choices, and environment variable names.
   - Treat generated `migration.local.json`, discovery JSON, plans, package downloads, and apply outputs as operator-local artifacts; do not commit or share them unless the user explicitly sanitizes them.
   - Use `references/config-example.json` only as a placeholder template.
4. Confirm source and target access:
   - Use `uip login status --output json`.
   - Switch tenants deliberately with `uip login tenant set "<tenant>" --output json` when needed.
   - For Standalone On-Prem where `uip login` does not persist, use `source.auth_mode: "direct_rest"` with External App env-var names.
   - Use `credential_asset_password_mode: "dummy"` for v1 unless the user provides a stronger secret handling process.
5. Generate the pre-migration analysis report before downloading packages or applying changes.
   - Prefer `scripts/uip_cloud_migration.py analyze --config migration.local.json --out migration-analysis.xlsx --plan-out migration-plan.json`.
   - This performs read-only metadata discovery/diffing and writes an Excel workbook for human approval plus the matching JSON plan for validation/apply.
   - The analysis workbook must include source/target counts, planned actions, skipped records, manual remediation, and approval steps.
   - Do not run `stage-packages`, `stage-libraries`, `validate`, or `apply` until the user confirms the analysis report.
   - For large tenants or offline rehearsals, reuse prior snapshots with `--source-discovery source.json --target-discovery target.json`.
6. After approval, download package and tenant-feed library binaries only when those entities are in scope.
   - Use `scripts/uip_cloud_migration.py stage-packages --config migration.local.json --discovery source.json --out package-stage-report.json` when a source discovery snapshot is available.
   - Use `scripts/uip_cloud_migration.py stage-libraries --config migration.local.json --discovery source.json --out library-stage-report.json` when tenant-feed libraries are in scope.
   - Package and library staging downloads `.nupkg` files into the configured staging folder. This is intentionally separate from analysis.
7. Validate before apply.
   - Use `scripts/uip_cloud_migration.py validate --config migration.local.json --plan migration-plan.json`.
   - Resolve validation errors before creating anything in Cloud.
8. Apply only after explicit user approval.
   - Use `scripts/uip_cloud_migration.py apply --config migration.local.json --plan migration-plan.json --yes`.
   - For On-Prem direct REST targets, start with `--max-actions 1` and inspect the result/target before increasing the limit.
   - Apply consumes an existing plan and refuses to run without it.

## Entity Order

Use this order for every migration plan:

1. `folders`
2. `credential_stores`
3. `roles`
4. `users`
5. `machines`
6. `robots`
7. `environments`
8. `assets`
9. `queues`
10. `storage_buckets`
11. `packages`
12. `libraries`
13. `processes`
14. `calendars`
15. `triggers`
16. `webhooks`
17. `feeds`
18. `settings`

This keeps dependencies stable: folders and tenant primitives first, folder-scoped resources next, packages before processes, and processes/queues/calendars before triggers. Legacy `robots` and `environments` are discovered for migration assessment but usually require Cloud-modernization decisions rather than direct apply.

## Exactness Boundaries

Treat "exact migration" as recreating all migratable Cloud-supported fields and relationships. Do not promise preservation of source IDs, audit metadata, read-only fields, runtime/job history, queue item history, robot session state, license consumption, identity-provider identities, credential store secrets, storage-provider secrets, bucket file contents, or real credential passwords.

Credential and secret values are not returned by ordinary Orchestrator metadata calls. In v1, create credential assets with the configured dummy password and report each credential asset requiring post-migration correction.

Automatic `apply` currently supports safe Cloud-native creation for folders, custom roles, machine templates, assets, queues, built-in storage buckets, packages, tenant-feed libraries, processes, calendars, triggers, and webhooks. Direct REST On-Prem target apply supports folders, custom roles, machine templates, assets, queues, packages, processes, and calendars; use canary limits before bulk apply. Identity Service user import is available only when `auto_import_users` is set to `true`. Credential stores, legacy robots/environments, host-feed libraries, feeds, settings, external storage buckets, built-in/static roles, and unconfirmed identity principals remain `manual_review` or skipped.

## Bundled Resources

- `scripts/uip_cloud_migration.py`: discovery, planning, validation, and controlled apply helper.
- `references/orchestrator-manager-2.6.2.md`: summarized behavior and limitations from the source Orchestrator Manager project and manual.
- `references/entity-mapping.json`: machine-readable Lift-and-Shift sheet mapping.
- `references/config-example.json`: starter config template.
