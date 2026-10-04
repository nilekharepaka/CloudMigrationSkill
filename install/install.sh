#!/usr/bin/env bash
# Installs the uipath-cloud-migration skill.
#
# One copy, one destination. The skill is self-contained: the migration engine
# and its documentation ship inside the skill folder, so there is nothing to
# install per project.
#
# Usage:
#   ./install/install.sh [--skill-root DIR] [--force]

set -euo pipefail

SKILL_ROOT="${HOME}/.autopilot/skills"
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --skill-root) SKILL_ROOT="$2"; shift 2 ;;
    --force)      FORCE=1; shift ;;
    -h|--help)    sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="${REPO_ROOT}/uipath-cloud-migration"
DESTINATION="${SKILL_ROOT}/uipath-cloud-migration"

echo
echo "uipath-cloud-migration installer"
echo "  from  ${SOURCE}"
echo "  to    ${DESTINATION}"
echo

[ -d "$SOURCE" ] || { echo "Skill folder not found: ${SOURCE}" >&2; exit 1; }

if [ -e "$DESTINATION" ] && [ "$FORCE" -ne 1 ]; then
  echo "${DESTINATION} already exists. Re-run with --force to replace it." >&2
  exit 1
fi

rm -rf "$DESTINATION"
mkdir -p "$SKILL_ROOT"
cp -R "$SOURCE" "$DESTINATION"
echo "  installed the skill, its engine, and its docs"

# --- prerequisites ----------------------------------------------------------
echo
echo "Prerequisites"

# On Windows, `python3` often resolves to a Microsoft Store stub that exists on
# PATH but fails when executed, so each candidate is actually run before use.
PYTHON=""
PY_VERSION=""
for candidate in python3 python py; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if probe="$("$candidate" -c 'import sys;print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"; then
    case "$probe" in
      [0-9]*.[0-9]*) PYTHON="$candidate"; PY_VERSION="$probe"; break ;;
    esac
  fi
done

if [ -z "$PYTHON" ]; then
  echo "  MISSING    no working python on PATH (3.10 or newer required)"
else
  major="${PY_VERSION%%.*}"; minor="${PY_VERSION##*.}"
  if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 10 ]; }; then
    echo "  TOO OLD    python ${PY_VERSION} (${PYTHON}) - the engine needs 3.10 or newer"
    PYTHON=""
  else
    echo "  ok         python ${PY_VERSION} (${PYTHON})"
  fi
fi

if command -v uip >/dev/null 2>&1; then
  echo "  ok         uip CLI"
else
  echo "  MISSING    uip CLI is not on PATH - required for Automation Cloud targets"
fi

# --- smoke check ------------------------------------------------------------
if [ -n "$PYTHON" ]; then
  if "$PYTHON" "${DESTINATION}/scripts/uip_cloud_migration.py" --help >/dev/null 2>&1; then
    echo "  ok         engine responds"
  else
    echo "  WARNING    the engine did not run cleanly"
  fi
fi

echo
echo "Done. Restart UiPath Studio/Autopilot, then ask:"
echo '  "Migrate my On-Prem Orchestrator tenant to Automation Cloud."'
echo
echo "Artifacts a migration produces (config, snapshots, plans, reports) are written to"
echo "your working directory. They contain full tenant inventories - do not commit them."
echo
