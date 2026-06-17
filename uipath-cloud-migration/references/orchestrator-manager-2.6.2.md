# Orchestrator Manager 2.6.2 Migration Reference

## Source Project

The source project is `OrchestratorManager`, a UiPath Studio Windows project using `Main.xaml`, `LiftAndShift.xaml`, `Orchestrator.xaml`, per-entity workflows under `Entities`, HTTP helpers under `Common` and `HTTP`, and Excel workbooks under `Workbooks`.

The project description says it performs bulk operations for users, robots, assets, machines, environments, processes, organization units, folders, queues, packages, libraries, and triggers. The 2.6 Lift-and-Shift feature is narrower and supports six migration entities. This skill extends discovery and diff planning beyond Lift-and-Shift to additional practical Orchestrator entities such as credential stores, roles, users, machines, storage buckets, libraries, calendars, webhooks, feeds, and settings.

## Lift-and-Shift Scope

The Lift-and-Shift workflow migrates:

- Folders
- Assets
- Queues
- Packages
- Processes
- Triggers

The documented flow is:

1. Input source instance details.
2. Authenticate to the source.
3. Select entities.
4. Run `Get` operations for the selected entities.
5. Copy data from each `Get` sheet to the corresponding create/upload sheet.
6. Input destination instance details.
7. Authenticate to the destination.
8. Run create/upload operations in the destination.

## Entity Mapping

| Entity | Source | Destination | Intermediate |
| --- | --- | --- | --- |
| folder | Get | Create | |
| asset | Get | Create | |
| asset credential | Get Credential | Create Credential | |
| queue | Get | Create | |
| package | Get | Uploadpackagelibrary | Downloadpackagelibrary |
| process | Get | Create | |
| trigger | Get | Create | |

Packages require a download step before upload to the target tenant.

## Config Concepts To Preserve

- `MaximumRequestsThreshold`, `RequestInterval`, and `RequestBatchSize` are safety controls.
- `DownloadedPackagesFolder` or the skill config `package_staging_folder` stores temporary packages.
- `EntitiesToProcess` maps to selected entities in the skill config.
- `EntityMappingFilePath`, `EntityNamesSheetName`, and `EntityMappingsSheetName` become `references/entity-mapping.json`.
- `GetCredentialAssetsViaRobot` is not used in v1; `credential_asset_password_mode: "dummy"` is used instead.

## Extended Discovery Scope

The helper supports broad discovery and plan diffs for:

- Tenant structure: folders, roles, users, machines, credential stores, calendars, feeds, settings.
- Classic/legacy assessment: robots and environments.
- Folder-scoped resources: assets, queues, storage buckets, processes/releases, triggers.
- Package resources: packages and libraries.
- Integration callbacks: webhooks.

Automatic apply is intentionally limited to entities with safe Cloud-native create/upload commands. Custom roles, machine templates, built-in storage buckets, calendars, and webhooks can now be applied automatically; credential stores, legacy robots/environments, libraries, feeds, settings, external storage buckets, built-in/static roles, and unconfirmed identity principals remain manual.

## Important Limitations

- Minimum source Orchestrator version in the original tool is 2018.4.
- Organization Units must be enabled for 2018.4 and 2019.4.
- Users, roles, machines, credential stores, storage buckets, webhooks, calendars, settings, robots, and environments are included in discovery/planning. Safe subsets are automatic; secret-bearing, legacy, built-in/static, or identity-dependent records remain manual.
- Real credential asset passwords cannot be copied through metadata discovery. Dummy credentials must be corrected after migration.
- Credential store provider secrets, storage-provider secrets, bucket file contents, source IDs, audit fields, runtime/job history, queue item history, and read-only server metadata are not migratable as-is.
