<#
.SYNOPSIS
    Installs the uipath-cloud-migration skill.

.DESCRIPTION
    One copy, one destination. The skill is self-contained: the migration engine
    and its documentation ship inside the skill folder, so there is nothing to
    install per project.

.PARAMETER SkillRoot
    UiPath Autopilot skills directory. Defaults to $HOME\.autopilot\skills.

.PARAMETER Force
    Replace an existing installed copy.

.EXAMPLE
    .\install\install.ps1
#>
[CmdletBinding()]
param(
    [string] $SkillRoot = (Join-Path $HOME ".autopilot\skills"),
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "uipath-cloud-migration"
$destination = Join-Path $SkillRoot "uipath-cloud-migration"

Write-Host ""
Write-Host "uipath-cloud-migration installer"
Write-Host "  from  $source"
Write-Host "  to    $destination"
Write-Host ""

if (-not (Test-Path $source)) {
    throw "Skill folder not found: $source"
}
if ((Test-Path $destination) -and -not $Force) {
    throw "$destination already exists. Re-run with -Force to replace it."
}
if (Test-Path $destination) {
    Remove-Item -Recurse -Force $destination
}

New-Item -ItemType Directory -Force $SkillRoot | Out-Null
Copy-Item -Recurse -Force $source $destination
Write-Host "  installed the skill, its engine, and its docs"

# --- prerequisites ----------------------------------------------------------
Write-Host ""
Write-Host "Prerequisites"

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    Write-Host "  MISSING    python is not on PATH (3.10 or newer required)"
} else {
    $version = (& python -c "import sys;print('%d.%d' % sys.version_info[:2])")
    $parts = $version.Split('.')
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
        Write-Host "  TOO OLD    python $version - the engine needs 3.10 or newer"
    } else {
        Write-Host "  ok         python $version"
    }
}

if ($null -eq (Get-Command uip -ErrorAction SilentlyContinue)) {
    Write-Host "  MISSING    uip CLI is not on PATH - required for Automation Cloud targets"
} else {
    Write-Host "  ok         uip CLI"
}

# --- smoke check ------------------------------------------------------------
$engine = Join-Path $destination "scripts\uip_cloud_migration.py"
try {
    & python $engine --help | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "  ok         engine responds" }
    else { Write-Host "  WARNING    engine returned exit $LASTEXITCODE" }
} catch {
    Write-Host "  WARNING    could not run the engine: $_"
}

Write-Host ""
Write-Host "Done. Restart UiPath Studio/Autopilot, then ask:"
Write-Host '  "Migrate my On-Prem Orchestrator tenant to Automation Cloud."'
Write-Host ""
Write-Host "Artifacts a migration produces (config, snapshots, plans, reports) are written to"
Write-Host "your working directory. They contain full tenant inventories - do not commit them."
Write-Host ""
