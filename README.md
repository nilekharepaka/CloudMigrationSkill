# UiPath Cloud Migration Skill

A review-first migration skill for moving supported UiPath Orchestrator entities from On-Premises Orchestrator or Automation Suite to Automation Cloud. It also supports pre-migration assessments and supported tenant-to-tenant copies.

## What it provides

- Fixed intake before any migration command
- Execution-plan approval gate
- Read-only discovery and analysis
- Excel and HTML analysis reports plus a machine-readable plan
- Manual-prerequisite and migration-boundary reporting
- Binary staging only after analysis approval
- Live-target validation before apply
- One-action canary, audit review, and bounded escalation
- Post-migration remediation guidance

The engine is deliberately conservative. It does not promise a byte-identical clone: source IDs, history, runtime state, real credential passwords, provider secrets, machine/robot keys, and other server-owned fields do not migrate.

## Package layout

```text
uipath-cloud-migration/
├── SKILL.md                 # Autopilot policy and approval gates
├── agents/openai.yaml       # Agent display metadata
├── docs/                    # Engine procedure and current CLI contract
├── references/              # Intake, entity, safety, and boundary policy
└── scripts/                 # Migration engine and smoke test
install/                     # Autopilot installers
assets/                      # Optional intake form
tests/tasks/                 # Smoke and fixture-analysis evaluation tasks
```

## Prerequisites

- UiPath Autopilot or UiPath Studio with the Autopilot skills directory available
- Python 3.10 or newer on `PATH`
- UiPath `uip` CLI on `PATH`
- A source External Application for direct REST On-Premises/Automation Suite discovery
- Access to the intended source and Automation Cloud target tenants

The engine uses only the Python standard library. No `pip install` step is required.

## Install for UiPath Autopilot

From the repository root on Windows PowerShell:

```powershell
.\install\install.ps1
```

From macOS/Linux/Git Bash:

```bash
./install/install.sh
```

The installers copy the skill to:

```text
~/.autopilot/skills/uipath-cloud-migration
```

Use `-Force` or `--force` only when intentionally replacing an existing installation. Restart UiPath Studio/Autopilot after installation so the skill catalog refreshes.

## Use

Ask Autopilot for a migration or assessment, for example:

> Migrate my On-Prem Orchestrator tenant to Automation Cloud. Ask for the complete intake first, produce the read-only analysis report, and wait for separate approval before staging or applying.

The skill reads the bundled documentation under `uipath-cloud-migration/docs/` before invoking the engine. Do not bypass its intake or approval gates.

## Local artifacts and Git safety

Migration configuration, snapshots, plans, reports, staged `.nupkg` files, and apply results are operator-local and may contain tenant inventories. Do not commit or share them unsanitized. The repository ignore rules cover common variants, including:

```text
migration.local.json
.local/
.migration-state/
*discovery*.json
*migration-plan*.json
*analysis*.xlsx
*analysis*.html
*apply*.json
*apply*.xlsx
DownloadedPackages/
*.nupkg
__pycache__/
```

## Evaluation tasks

The task definitions under `tests/tasks/uipath-cloud-migration/` cover:

- Fixed-intake enforcement before migration commands
- Fixture-based assessment analysis without network access or apply

No live tenant, credential, report, snapshot, package, or apply artifact belongs in this repository.
