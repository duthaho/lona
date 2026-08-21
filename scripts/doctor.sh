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

# ---- Probe engine ----------------------------------------------------------
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
HTTP_TIMEOUT="${DOCTOR_HTTP_TIMEOUT:-15}"

# Tiered probe [D2]: one free listing fetch catches rotated/deprecated ids;
# a 1-token completion (real availability, incl. 429 congestion) is spent
# only on the primary — or on every model with --deep, on none with --quick.
LISTING_OK=0
LISTING_BODY=""
fetch_listing() {
  if LISTING_BODY="$(curl -fsS --max-time "$HTTP_TIMEOUT" \
      "$OPENROUTER_BASE_URL/models" 2>/dev/null)"; then
    LISTING_OK=1
  else
    warn "OpenRouter model listing unavailable — listing tier degraded to ERROR"
  fi
}

listed() { printf '%s' "$LISTING_BODY" | grep -qF "\"$1\""; }

status_by_code() { # http-code -> OK|LIMITED|DEAD|ERROR
  case "$1" in
    200)     echo OK ;;
    429)     echo LIMITED ;;
    400|404) echo DEAD ;;
    *)       echo ERROR ;;   # 5xx, auth failures, curl timeout (000)
  esac
}

# 1-token completion probe -> status by HTTP code
probe_completion_openrouter() { # model-id -> OK|LIMITED|DEAD|ERROR
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$HTTP_TIMEOUT" \
    -X POST "$OPENROUTER_BASE_URL/chat/completions" \
    -H "Authorization: Bearer $(env_val OPENROUTER_API_KEY)" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$1\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}" \
    || echo 000)"
  status_by_code "$code"
}

# Gemini (Google AI Studio) has no free listing tier — completion only [D7].
GEMINI_BASE_URL="${GEMINI_BASE_URL:-https://generativelanguage.googleapis.com}"
probe_completion_gemini() { # model-id -> OK|LIMITED|DEAD|ERROR
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$HTTP_TIMEOUT" \
    -X POST "$GEMINI_BASE_URL/v1beta/models/$1:generateContent" \
    -H "x-goog-api-key: $(env_val GOOGLE_API_KEY)" \
    -H 'Content-Type: application/json' \
    -d '{"contents":[{"parts":[{"text":"ping"}]}],"generationConfig":{"maxOutputTokens":1}}' \
    || echo 000)"
  status_by_code "$code"
}

# ---- Telegram notification [D4] --------------------------------------------
# DM the owner only when the health state changes (state persisted in
# data/doctor-state-<platform>), including a recovery message. On delivery
# failure the state is NOT persisted, so the next run retries the alert.
TELEGRAM_API_BASE="${TELEGRAM_API_BASE:-https://api.telegram.org}"

notify() { # rc results
  local rc="$1" results="$2" state_file="data/doctor-state-$PLATFORM"
  local state issues="" prev token chat_id msg code status id tier
  while IFS='|' read -r status id tier; do
    [ -n "$status" ] || continue
    case "$status" in OK|SKIP) ;; *) issues="${issues}${id}=${status} " ;; esac
  done <<EOF
$results
EOF
  state="$rc:$(printf '%s\n' $issues | sort | tr '\n' ' ')"
  prev="$(cat "$state_file" 2>/dev/null || true)"
  [ "$state" = "$prev" ] && return 0
  if [ "$rc" = 0 ] && [ -z "$prev" ]; then
    # First observation and it's healthy — record silently, no DM.
    printf '%s\n' "$state" > "$state_file"
    return 0
  fi
  token="$(env_val TELEGRAM_BOT_TOKEN)"
  chat_id="$(env_val TELEGRAM_ALLOWED_USERS | tr -d ' ' | cut -d, -f1)"
  if [ -z "$token" ] || [ -z "$chat_id" ]; then
    warn "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USERS) — cannot notify."
    return 0
  fi
  if [ "$rc" = 0 ]; then
    msg="✅ Lona doctor ($PLATFORM): model chain healthy again."
  else
    msg="⚠️ Lona doctor ($PLATFORM): model chain issue — ${issues:-degraded}· run: ./deploy.sh $PLATFORM doctor"
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$HTTP_TIMEOUT" \
    -X POST "$TELEGRAM_API_BASE/bot$token/sendMessage" \
    --data-urlencode "chat_id=$chat_id" --data-urlencode "text=$msg" \
    || echo 000)"
  if [ "$code" = 200 ]; then
    printf '%s\n' "$state" > "$state_file"
  else
    warn "Telegram notify failed (HTTP $code) — state kept, will retry next run."
  fi
}

run_probe() {
  local chain results="" idx=0 line provider id status tier
  chain="$(extract_chain)"
  if [ -z "$chain" ]; then
    warn "No probeable models found in the $PLATFORM config."
    exit 1
  fi
  fetch_listing
  while IFS= read -r line; do
    provider="${line%%:*}"
    id="${line#*:}"
    case "$provider" in
      openrouter)
        # Listing tier first — a rotated id is DEAD, no quota spent on it.
        if [ "$LISTING_OK" = 1 ]; then
          if listed "$id"; then status=OK tier=listing; else status=DEAD tier=listing; fi
        else
          status=ERROR tier=listing
        fi
        # Completion tier: primary by default, every listed-OK model with
        # --deep, nobody with --quick [D2, D5]. A listing-DEAD id never
        # costs a completion request.
        if [ "$status" != DEAD ] && [ "$MODE" != quick ] \
          && { [ "$idx" = 0 ] || [ "$MODE" = deep ]; }; then
          status="$(probe_completion_openrouter "$id")" tier=completion
        fi
        ;;
      gemini)
        # Completion-only; skipped under --quick and for non-primary members
        # in default mode (SKIP is exit-code-neutral: a quick deploy check
        # must not cry wolf on every Gemini setup).
        if [ "$MODE" = quick ] \
          || { [ "$idx" != 0 ] && [ "$MODE" != deep ]; }; then
          status=SKIP tier=quick
        elif [ -z "$(env_val GOOGLE_API_KEY)" ]; then
          status="ERROR" tier="no GOOGLE_API_KEY"
        else
          status="$(probe_completion_gemini "$id")" tier=completion
        fi
        ;;
    esac
    results="${results}${status}|${id}|${tier}
"
    idx=$((idx + 1))
  done <<EOF
$chain
EOF

  # Report + exit code [D6]
  local rc=0 first=1 summary report=""
  while IFS='|' read -r status id tier; do
    [ -n "$status" ] || continue
    report="$report$(printf '  %-7s %s (%s)' "$status" "$id" "$tier")
"
    if [ "$first" = 1 ]; then
      [ "$status" = OK ] || [ "$status" = SKIP ] || rc=2
      first=0
    elif [ "$status" != OK ] && [ "$status" != SKIP ] && [ "$rc" = 0 ]; then
      rc=1
    fi
  done <<EOF
$results
EOF
  case "$rc" in
    0) summary="chain healthy" ;;
    1) summary="chain degraded (fallback issues)" ;;
    2) summary="primary unusable" ;;
  esac
  # --quiet (cron hygiene): stay silent when healthy, always report trouble.
  if [ "$QUIET" = 0 ] || [ "$rc" != 0 ]; then
    printf '%s' "$report"
    say "$PLATFORM model chain: $summary"
  fi
  [ "$NOTIFY" = 1 ] && notify "$rc" "$results"
  exit "$rc"
}

# ---- Scheduled runs via host cron [D3] -------------------------------------
CRONTAB_CMD="${CRONTAB_CMD:-crontab}"

cron_schedule() {
  local s="${DOCTOR_CRON_SCHEDULE:-}"
  [ -n "$s" ] || s="$(env_val DOCTOR_CRON_SCHEDULE)"
  [ -n "$s" ] || s="0 */6 * * *"
  printf '%s' "$s"
}

cron_install() {
  local marker="# lona-doctor-$PLATFORM" entry current
  # Repo path single-quoted: cron lines are parsed by sh, spaces must survive.
  entry="$(cron_schedule) cd '$PWD' && ./deploy.sh $PLATFORM doctor --notify --quiet $marker"
  current="$("$CRONTAB_CMD" -l 2>/dev/null | grep -vF "$marker" || true)"
  printf '%s\n%s\n' "$current" "$entry" | sed '/^$/d' | "$CRONTAB_CMD" -
  say "Installed cron entry: $entry"
}

cron_uninstall() {
  local marker="# lona-doctor-$PLATFORM" current
  current="$("$CRONTAB_CMD" -l 2>/dev/null | grep -vF "$marker" || true)"
  printf '%s\n' "$current" | sed '/^$/d' | "$CRONTAB_CMD" -
  say "Removed cron entry for $PLATFORM doctor (if present)."
}

case "$ACTION" in
  install)   cron_install ;;
  uninstall) cron_uninstall ;;
  probe)     run_probe ;;
esac
