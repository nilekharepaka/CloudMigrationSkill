---
name: uipath-cloud-migration
description: Migrate UiPath Orchestrator entities from On-Premises or Automation Suite to Automation Cloud. Use for Cloud migration discovery, diffing, planning, validation, and controlled apply of folders, credential stores, users, roles, machines, assets, queues, buckets, packages, libraries, processes, triggers, webhooks, calendars, and legacy assessment entities with uip CLI or direct On-Prem REST.
---

# UiPath Cloud Migration

## Purpose

Use this skill to migrate practical UiPath Orchestrator entities from an On-Premises source tenant to an Automation Cloud target tenant. The helper supports broad discovery and diff planning. Automatic apply remains deliberately conservative and is limited to entity types with implemented safe create/upload commands.

Always load and follow `uipath-platform` before executing commands that touch UiPath Cloud or Orchestrator. Prefer `uip` CLI command families. Use raw REST only after checking `uipath-platform/references/uip-commands.md` and confirming no `uip` command covers the operation.

## Workflow

1. Collect source and target details for this run.
   - Ask for the On-Prem tenant name, Identity URL, Orchestrator URL, and target Automation Cloud tenant.
   - Ask whether the On-Prem source should use `direct_rest` External App auth or `uip` auth.
   - Never save client secrets, user keys, passwords, or access tokens in the skill, examples, repository files, migration plan, or discovery snapshots.
   - Use environment variables for External App credentials, for example `UIP_ONPREM_CLIENT_ID` and `UIP_ONPREM_CLIENT_SECRET`.
2. Create a local, unshared migration config.
   - Prefer `scripts/uip_cloud_migration.py init-config --out migration.local.json`.
   - The interactive setup writes only tenant names, URLs, folder scope, entity choices, and environment variable names.
   - Treat generated `migration.local.json`, discovery JSON, plans, package downloads, and apply outputs as operator-local artifacts; do not commit or share them unless the user explicitly sanitizes them.
   - Use `references/config-example.json` only as a placeholder template.
3. Confirm source and target access:
   - Use `uip login status --output json`.
   - Switch tenants deliberately with `uip login tenant set "<tenant>" --output json` when needed.
   - For Standalone On-Prem where `uip login` does not persist, use `source.auth_mode: "direct_rest"` with External App env-var names.
   - Use `credential_asset_password_mode: "dummy"` for v1 unless the user provides a stronger secret handling process.
4. Run discovery for both sides.
   - Use `scripts/uip_cloud_migration.py discover --config migration.local.json --side source`.
   - Use `scripts/uip_cloud_migration.py discover --config migration.local.json --side target`.
5. Generate the migration plan.
   - Use `scripts/uip_cloud_migration.py plan --config migration.local.json --out migration-plan.json`.
   - For large tenants or offline rehearsals, reuse prior snapshots with `--source-discovery source.json --target-discovery target.json`.
   - Review skipped records, manual remediation, and actions in dependency order.
6. Validate before apply.
   - Use `scripts/uip_cloud_migration.py validate --config migration.local.json --plan migration-plan.json`.
   - Resolve validation errors before creating anything in Cloud.
7. Apply only after explicit user approval.
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

Automatic `apply` currently supports safe Cloud-native creation for folders, custom roles, machine templates, assets, queues, built-in storage buckets, packages, processes, calendars, triggers, and webhooks. Direct REST On-Prem target apply supports folders, custom roles, machine templates, assets, queues, packages, processes, and calendars; use canary limits before bulk apply. Identity Service user import is available only when `auto_import_users` is set to `true`. Credential stores, legacy robots/environments, libraries, feeds, settings, external storage buckets, built-in/static roles, and unconfirmed identity principals remain `manual_review`.

## Bundled Resources

- `scripts/uip_cloud_migration.py`: discovery, planning, validation, and controlled apply helper.
- `scripts/test_uip_cloud_migration.py`: fixture-based smoke tests for the helper.
- `references/orchestrator-manager-2.6.2.md`: summarized behavior and limitations from the source Orchestrator Manager project and manual.
- `references/entity-mapping.json`: machine-readable Lift-and-Shift sheet mapping.
- `references/config-example.json`: starter config template.
