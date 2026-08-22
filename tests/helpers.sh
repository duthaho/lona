#!/usr/bin/env bash
# Shared helpers for tests/*_test.sh — source, don't execute.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Copy the deployable bits into an isolated sandbox. The space in the path
# is deliberate: installed cron entries must survive it.
sandbox_setup() {
  SANDBOX="$(mktemp -d)/lona repo"
  mkdir -p "$SANDBOX"
  cp "$REPO_ROOT/deploy.sh" "$SANDBOX/"
  cp -r "$REPO_ROOT/scripts" "$SANDBOX/scripts"
  cp -r "$REPO_ROOT/config" "$SANDBOX/config"
  cat > "$SANDBOX/.env" <<'EOF'
OPENROUTER_API_KEY=test-or-key
TELEGRAM_BOT_TOKEN=test-tg-token
TELEGRAM_ALLOWED_USERS=1111,2222
EOF
}

sandbox_teardown() { rm -rf "$(dirname "$SANDBOX")"; }

# Start the fixture-driven HTTP stub (OpenRouter/Telegram/Gemini). Exports
# STUB_DIR (fixtures + requests.log) and STUB_URL. Pair with stub_stop.
stub_start() {
  STUB_DIR="$(dirname "$SANDBOX")/stub"
  mkdir -p "$STUB_DIR"
  export STUB_DIR
  python3 "$REPO_ROOT/tests/stub_server.py" &
  STUB_PID=$!
  local i=0
  while [ ! -f "$STUB_DIR/port" ]; do
    i=$((i + 1))
    [ "$i" -le 50 ] || { echo "stub server did not start" >&2; exit 1; }
    sleep 0.1
  done
  STUB_URL="http://127.0.0.1:$(cat "$STUB_DIR/port")"
  export STUB_URL
}

stub_stop() { [ -n "${STUB_PID:-}" ] && kill "$STUB_PID" 2>/dev/null || true; }

# Stateful fake `docker` for the transactional-update tests. Exports
# DOCKER_STATE (fixture + cmd.log dir) and prepends a bin dir with the shim to
# PATH. Optional fixtures under DOCKER_STATE: container_image, tag_id, pull_id,
# status, health, corrupt_on_up. Model of the world:
#   compose pull  → copy pull_id → tag_id (a new image lands on the ref)
#   compose up    → copy tag_id → container_image (container adopts the ref
#                   image); if corrupt_on_up exists, corrupt
#                   data/<profile>/state.txt once (a bad image mutating state)
#   tag <id> <ref>→ set tag_id → <id> (rollback retag)
# Every compose verb and tag is appended to cmd.log. Call after sandbox_setup.
fake_docker_setup() {
  DOCKER_STATE="$(dirname "$SANDBOX")/docker-state"
  mkdir -p "$DOCKER_STATE"
  export DOCKER_STATE
  local bin="$(dirname "$SANDBOX")/dockerbin"
  mkdir -p "$bin"
  cat > "$bin/docker" <<'SH'
#!/usr/bin/env bash
S="$DOCKER_STATE"
log() { echo "$*" >> "$S/cmd.log"; }
get() { cat "$S/$1" 2>/dev/null || true; }
put() { printf '%s' "$2" > "$S/$1"; }
case "${1:-}" in
  compose)
    shift; verb=""; prof=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --profile) prof="$2"; shift 2 ;;
        pull|up|stop|down|run) verb="$1"; shift; break ;;
        *) shift ;;
      esac
    done
    [ -n "$prof" ] && put profile "$prof"
    log "compose $verb"
    case "$verb" in
      pull) [ -f "$S/pull_id" ] && cp "$S/pull_id" "$S/tag_id" || true ;;
      up)
        [ -f "$S/tag_id" ] && cp "$S/tag_id" "$S/container_image" || true
        if [ -f "$S/corrupt_on_up" ] && [ -n "$(get profile)" ]; then
          echo corrupted > "data/$(get profile)/state.txt" 2>/dev/null || true
          rm -f "$S/corrupt_on_up"
        fi ;;
    esac
    exit 0 ;;
  image) log "image inspect"; get tag_id; exit 0 ;;
  inspect)
    shift; fmt=""
    while [ $# -gt 0 ]; do
      case "$1" in --format) fmt="$2"; shift 2 ;; *) shift ;; esac
    done
    case "$fmt" in
      *.Image*) get container_image ;;
      *State.Health.Status*) get health ;;
      *State.Status*) get status ;;
    esac
    exit 0 ;;
  tag)
    log "tag $2"
    [ -f "$S/tag_fail" ] && exit 1   # simulate a rollback that can't complete
    put tag_id "$2"; exit 0 ;;
  *) log "unknown $*"; exit 0 ;;
esac
SH
  chmod +x "$bin/docker"
  export PATH="$bin:$PATH"
}

# Checksums of everything under data/ except the doctor's own state file
# (the one file it is allowed to write) [D1, AC7]. Run from $SANDBOX.
data_checksums() {
  find data -type f ! -name 'doctor-state-*' -print0 2>/dev/null \
    | sort -z | xargs -0 -r sha256sum
}

fail() { echo "  assert failed: $*" >&2; exit 1; }

assert_eq() { # expected actual [label]
  [ "$1" = "$2" ] || fail "${3:-} expected '$1', got '$2'"
}

assert_contains() { # haystack needle [label]
  case "$1" in *"$2"*) ;; *) fail "${3:-} output missing '$2': $1" ;; esac
}
