#!/usr/bin/env bash
# Lona model-chain doctor — probes the platform's model chain for health.
#
#   scripts/doctor.sh <openclaw|hermes> [--quick|--deep] [--notify] [--quiet]
#   scripts/doctor.sh <openclaw|hermes> install|uninstall
#
# Normally invoked via: ./deploy.sh <platform> doctor [flags]
# Exit codes: 0 chain healthy · 1 degraded (fallback issues) · 2 primary
# unusable · 64 usage error.
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  sed -n '4,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 64
}

PLATFORM="${1:-}"
[ $# -ge 1 ] && shift
case "$PLATFORM" in
  openclaw|hermes) ;;
  *) usage ;;
esac

MODE=default ACTION=probe NOTIFY=0 QUIET=0
for arg in "$@"; do
  case "$arg" in
    --quick)   MODE=quick ;;
    --deep)    MODE=deep ;;
    --notify)  NOTIFY=1 ;;
    --quiet)   QUIET=1 ;;
    install)   ACTION=install ;;
    uninstall) ACTION=uninstall ;;
    *) usage ;;
  esac
done
