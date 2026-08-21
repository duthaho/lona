#!/usr/bin/env bash
# Lona model-chain doctor — probes the platform's model chain for health.
#
#   scripts/doctor.sh <openclaw|hermes> [--quick|--deep] [--notify] [--quiet]
#   scripts/doctor.sh <openclaw|hermes> install|uninstall
#   scripts/doctor.sh <openclaw|hermes> --print-chain   # show the parsed chain
#
# Normally invoked via: ./deploy.sh <platform> doctor [flags]
# Exit codes: 0 chain healthy · 1 degraded (fallback issues) · 2 primary
# unusable · 64 usage error.
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  sed -n '4,10p' "$0" | sed 's/^# \{0,1\}//'
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
    --quick)       MODE=quick ;;
    --deep)        MODE=deep ;;
    --notify)      NOTIFY=1 ;;
    --quiet)       QUIET=1 ;;
    --print-chain) ACTION=print-chain ;;
    install)       ACTION=install ;;
    uninstall)     ACTION=uninstall ;;
    *) usage ;;
  esac
done

# ---- Chain extraction ------------------------------------------------------
# Emits one line per chain member, primary first:
#   openrouter:<id>   probed via OpenRouter (listing + completion tiers)
#   gemini:<id>       probed via Google AI Studio (completion only)
# Grep-based and format-tolerant by design (configs are JSON5/YAML with
# comments). Subscription primaries (openai/anthropic) are not the free
# chain's concern and are skipped.

config_file() {
  case "$PLATFORM" in
    openclaw)
      if [ -f data/openclaw/openclaw.json ]; then echo data/openclaw/openclaw.json
      else echo config/openclaw/openclaw.json; fi ;;
    hermes)
      if [ -f data/hermes/config.yaml ]; then echo data/hermes/config.yaml
      else echo config/hermes/config.yaml; fi ;;
  esac
}

emit_model() { # raw model ref -> chain line (or nothing)
  local ref="$1"
  case "$ref" in
    openrouter/*) echo "openrouter:${ref#openrouter/}" ;;
    google/gemini*) echo "gemini:${ref#google/}" ;;
    gemini*) echo "gemini:$ref" ;;
    *) : ;; # subscription/unknown provider — not part of the free chain
  esac
}

extract_chain_openclaw() {
  local f stripped primary ref
  f="$(config_file)"
  # Drop full-line // comments, then read primary + quoted openrouter refs.
  stripped="$(sed 's|^[[:space:]]*//.*||' "$f")"
  primary="$(printf '%s\n' "$stripped" \
    | grep -o 'primary:[[:space:]]*"[^"]*"' | head -1 \
    | sed 's/.*"\(.*\)"/\1/')" || true
  [ -n "$primary" ] && emit_model "$primary"
  printf '%s\n' "$stripped" | grep -o '"openrouter/[^"]*"' | tr -d '"' \
    | while IFS= read -r ref; do
        [ "$ref" = "$primary" ] && continue
        emit_model "$ref"
      done
}

extract_chain_hermes() {
  local f stripped
  f="$(config_file)"
  stripped="$(sed 's|^[[:space:]]*#.*||' "$f")"
  # model: / fallback_model: blocks -> "<provider> <model>" pairs, then
  # auxiliary.openrouter_model. A block ends at the next top-level key.
  printf '%s\n' "$stripped" | awk '
    /^[a-z_]+:/ { block = $1; sub(":", "", block); provider = "" }
    block == "model" && $1 == "provider:" { provider = $2 }
    block == "model" && $1 == "default:" { print provider, $2 }
    block == "fallback_model" && $1 == "provider:" { provider = $2 }
    block == "fallback_model" && $1 == "model:" { print provider, $2 }
    block == "auxiliary" && $1 == "openrouter_model:" { print "openrouter", $2 }
  ' | while read -r provider model; do
    [ -n "$model" ] || continue
    case "$provider" in
      openrouter|"") echo "openrouter:$model" ;;
      gemini) echo "gemini:$model" ;;
      *) : ;; # subscription provider — skipped
    esac
  done
}

extract_chain() {
  case "$PLATFORM" in
    openclaw) extract_chain_openclaw ;;
    hermes)   extract_chain_hermes ;;
  esac
}

if [ "$ACTION" = print-chain ]; then
  extract_chain
  exit 0
fi
