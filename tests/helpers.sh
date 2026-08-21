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

fail() { echo "  assert failed: $*" >&2; exit 1; }

assert_eq() { # expected actual [label]
  [ "$1" = "$2" ] || fail "${3:-} expected '$1', got '$2'"
}

assert_contains() { # haystack needle [label]
  case "$1" in *"$2"*) ;; *) fail "${3:-} output missing '$2': $1" ;; esac
}
