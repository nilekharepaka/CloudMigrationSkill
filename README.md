# UiPath Cloud Migration Skill

Codex skill for assessing and migrating supported UiPath Orchestrator entities from an On-Premises or Automation Suite source tenant to an Automation Cloud target tenant.

The skill is designed to be review-first: it creates a pre-migration analysis report before downloading packages/libraries or creating anything in the target tenant.

## What This Skill Does

- Discovers source On-Prem/Automation Suite Orchestrator metadata using direct Orchestrator REST API calls authenticated with an External Application.
- Discovers and applies target Automation Cloud changes using the UiPath `uip` CLI.
- Compares source entities against the target tenant.
- Creates an Excel analysis report for review and approval.
- Creates a JSON migration plan used by validation and apply.
- Stages package and tenant-feed library `.nupkg` files only after approval.
- Validates staged artifacts before apply.
- Applies supported entities in dependency order.

## Installation

Run this command from PowerShell:

```powershell
python "$env:USERPROFILE\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py" --repo nilekharepaka/CloudMigrationSkill --path uipath-cloud-migration
```

Restart Codex after installation.

## Prerequisites

- Codex desktop or Codex environment with skill support.
- Python available on the machine.
- UiPath `uip` CLI installed and available on `PATH` for Automation Cloud target discovery and apply.
- Automation Cloud access for the target tenant through `uip login`.
- On-Prem or Automation Suite External Application with the required scopes for source REST API discovery.
- Target tenant permissions to create the selected supported entities.

Recommended source External App scopes:

```text
OR.Folders OR.Assets OR.Queues OR.Execution OR.Settings OR.Administration OR.Jobs OR.Users OR.Robots OR.Machines OR.Webhooks OR.License
```

## Authentication and API Usage

This skill intentionally uses different access methods for source and target:

| Side | System | Access Method | Why |
|---|---|---|---|
| Source | On-Premises Orchestrator or Automation Suite | Direct REST API with External Application client credentials | On-Prem browser/CLI login can be unreliable or unavailable for repeatable extraction. REST gives deterministic read-only discovery and package/library download behavior. |
| Target | Automation Cloud | UiPath `uip` CLI | The `uip` CLI is the preferred supported path for Cloud tenant switching, discovery, and safe create/upload operations. |

For the source, `migration.local.json` should normally use:

```json
{
  "source": {
    "auth_mode": "direct_rest",
    "identity_url": "https://<onprem-host>/identity",
    "orchestrator_url": "https://<onprem-host>",
    "client_id_env": "UIP_ONPREM_CLIENT_ID",
    "client_secret_env": "UIP_ONPREM_CLIENT_SECRET"
  }
}
```

For the target, log in and select the Automation Cloud tenant with `uip`:

```powershell
uip login --output json
uip login tenant set "<target-cloud-tenant>" --output json
```

## First-Time Setup

Before running any migration command, collect and confirm the full source and target details.

Source details to confirm:

- Source system type: On-Premises Orchestrator or Automation Suite
- Source tenant name
- Source Identity URL
- Source Orchestrator URL
- Source auth mode, normally `direct_rest`
- External App client ID environment variable name
- External App client secret environment variable name
- External App scopes
- Folder scope to migrate
- Entity scope to migrate
- Package staging folder
- Credential asset dummy password policy
- Webhook temporary secret policy
- Request interval and batch size

Target details to confirm:

- Automation Cloud URL or authority, if non-default
- Automation Cloud organization, if relevant
- Target tenant name
- Target `uip login` status
- Target folder scope
- Expected apply mode
- Canary batch size for first apply

Before continuing, present or review the planned execution sequence:

```text
1. Create local config with init-config.
2. Run analyze to generate migration-analysis.xlsx and migration-plan.json.
3. Review and approve the analysis report.
4. Stage package/library .nupkg files only after approval.
5. Validate the plan and staged artifacts.
6. Run a small canary apply.
7. Run full apply only after canary review and explicit approval.
```

Create a local migration config. This file is for your machine only and should not be committed.

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" init-config --out migration.local.json
```

The setup prompts for:

- Source tenant name
- Source Identity URL
- Source Orchestrator URL
- Source auth mode, normally `direct_rest` for On-Prem/Automation Suite REST API access
- Environment variable names for source External App credentials
- Target Automation Cloud tenant name
- Folder scope
- Entity scope
- Package staging folder

The config stores environment variable names, not secrets.

Set the source External App credentials in the same PowerShell session:

```powershell
$env:UIP_ONPREM_CLIENT_ID = "<source-external-app-id>"
$env:UIP_ONPREM_CLIENT_SECRET = "<source-external-app-secret>"
```

Log in to Automation Cloud for the target tenant:

```powershell
uip login --output json
uip login tenant set "<target-cloud-tenant>" --output json
```

## Recommended Execution Flow

### 1. Generate Analysis Report

Run analysis first. This is read-only metadata discovery and diffing. It does not download package files and does not create anything in Cloud.

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" analyze --config migration.local.json --out migration-analysis.xlsx --plan-out migration-plan.json
```

Review these files:

```text
migration-analysis.xlsx
migration-plan.json
```

The Excel workbook includes:

- Summary
- Entity Summary
- Planned Actions
- Skipped
- Manual Remediation
- Approval Runbook

Use the Excel workbook for stakeholder sign-off. Keep the JSON plan because validation and apply use it.

### 2. Approve the Scope

Before continuing, confirm:

- The selected entities are expected.
- The target tenant is correct.
- Skipped items are acceptable.
- Manual remediation items are understood.
- Credential assets can be created with a dummy password and corrected later.
- Package/library staging is allowed.

### 3. Stage Packages and Libraries After Approval

If packages are in scope:

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" stage-packages --config migration.local.json --out package-stage-report.json
```

If tenant-feed libraries are in scope:

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" stage-libraries --config migration.local.json --out library-stage-report.json
```

These commands download `.nupkg` files into the configured package staging folder.

### 4. Validate

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" validate --config migration.local.json --plan migration-plan.json
```

Resolve validation errors before apply.

### 5. Canary Apply

Start with a small canary batch.

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" apply --config migration.local.json --plan migration-plan.json --max-actions 1 --yes --out apply-canary-1.json
```

Inspect the target tenant. If the result is correct, increase gradually:

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" apply --config migration.local.json --plan migration-plan.json --max-actions 25 --yes --out apply-canary-25.json
```

### 6. Full Apply

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" apply --config migration.local.json --plan migration-plan.json --yes --out apply-results.json
```

## Migration Scope

The skill discovers and plans these entities:

| Entity | Discovery / Planning | Automatic Cloud Apply |
|---|---:|---:|
| Folders | Yes | Yes |
| Credential stores | Yes | No |
| Roles | Yes | Custom roles only |
| Users | Yes | Only when `auto_import_users=true` and identity dependencies are valid |
| Machines / machine templates | Yes | Yes |
| Robots | Yes | No |
| Environments | Yes | No |
| Assets | Yes | Yes |
| Queues | Yes | Yes |
| Storage buckets | Yes | Built-in storage buckets only |
| Packages | Yes | Yes, after package staging |
| Libraries | Yes | Tenant-feed libraries only, after library staging |
| Processes | Yes | Yes |
| Calendars | Yes | Yes |
| Triggers | Yes | Yes |
| Webhooks | Yes | Yes, with temporary secret |
| Feeds | Yes | No |
| Settings | Yes | No |

Automatic apply to Automation Cloud currently supports:

```text
folders
custom roles
users only when explicitly enabled
machine templates
assets
queues
built-in storage buckets
packages
tenant-feed libraries
processes
calendars
triggers
webhooks
```

## Out of Scope / Manual Review

These items are not fully automatic and must be reviewed manually:

- Credential stores and provider secrets
- Real credential asset passwords
- Classic robots
- Classic environments
- Host-feed libraries
- Feeds
- Tenant settings
- External storage bucket provider configuration
- Built-in/static roles
- Identity principals that cannot be matched/imported in Cloud
- Runtime/job history
- Queue item history
- Audit history
- Source IDs and read-only metadata
- License consumption/state
- Robot session state

## Important Exactness Boundaries

"Exact migration" means recreating migratable, Cloud-supported fields and relationships as closely as possible.

The skill cannot preserve:

- Source entity IDs
- Audit metadata
- Runtime history
- Job history
- Queue item history
- Real credential passwords
- Credential store secrets
- Storage provider secrets
- Read-only fields

Credential assets are created with a dummy password. The analysis report and plan identify those assets so the password can be corrected after migration.

## Files You Should Not Commit

Do not commit local config, discovery, plan, report, package, or apply artifacts:

```text
migration.local.json
*-discovery*.json
*migration-plan*.json
*analysis*.xlsx
*apply*.json
*stage-report*.json
DownloadedPackages/
```

The repository `.gitignore` already excludes these patterns.

## Troubleshooting

Check active Cloud login:

```powershell
uip login status --output json
uip login tenant list --output json
uip login tenant set "<target-cloud-tenant>" --output json
```

Show command help:

```powershell
python "$env:USERPROFILE\.codex\skills\uipath-cloud-migration\scripts\uip_cloud_migration.py" --help
```
