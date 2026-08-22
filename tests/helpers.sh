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
