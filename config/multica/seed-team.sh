#!/usr/bin/env bash
# Seed missing Multica team assets without overwriting live customization.
# MULTICA_API_TOKEN=... ./deploy.sh multica team-setup [--workspace-id UUID]
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'python3 is required' >&2; exit 1; }
exec python3 scripts/multica_team.py "$@"