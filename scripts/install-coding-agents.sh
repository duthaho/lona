#!/usr/bin/env bash
# Install AI coding agent CLIs on this machine for use with Multica.
# Installs: multica CLI, Claude Code, Codex CLI
#
#   ./deploy.sh multica install-agents
#   # or directly:
#   bash scripts/install-coding-agents.sh
set -euo pipefail

cd "$(dirname "$0")/.."

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m✓\033[0m  %s\n' "$*"; }

already() {
  local cmd="$1" label="$2"
  # command -v checks $PATH; also probe npm global bin for Node-based CLIs.
  local npm_bin=""
  command -v npm >/dev/null 2>&1 \
    && npm_bin="$(npm config get prefix 2>/dev/null)/bin" || true
  for loc in "" "$npm_bin"; do
    local full="${loc:+$loc/}$cmd"
    if command -v "$full" >/dev/null 2>&1 || [ -x "$full" ]; then
      ok "$label already installed (${full})"
      # Bring npm global bin onto PATH so subsequent steps find the binary
      [ -n "$npm_bin" ] && export PATH="$npm_bin:$PATH" || true
      return 0
    fi
  done
  return 1
}

ensure_node() {
  command -v node >/dev/null 2>&1 && return 0
  say "Node.js not found — installing via fnm"
  curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell
  export PATH="$HOME/.fnm:$PATH"
  eval "$(fnm env 2>/dev/null || true)"
  fnm install --lts
  fnm use lts-latest
  command -v node >/dev/null 2>&1 || die "Node.js install failed — install manually: https://nodejs.org"
}

# ── multica CLI ───────────────────────────────────────────────────────────────
say "multica CLI"
if ! already multica "multica CLI"; then
  if command -v brew >/dev/null 2>&1; then
    brew install multica-ai/tap/multica
  else
    curl -fsSL https://raw.githubusercontent.com/multica-ai/multica/main/scripts/install.sh | bash
    export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
  fi
  already multica "multica CLI" || warn "multica not on PATH yet — open a new shell or add it manually"
fi

# ── Claude Code ───────────────────────────────────────────────────────────────
say "Claude Code"
if ! already claude "Claude Code"; then
  ensure_node
  npm install -g @anthropic-ai/claude-code
  already claude "Claude Code" || warn "claude not on PATH — check: npm root -g"
fi

# ── Codex CLI ─────────────────────────────────────────────────────────────────
say "Codex CLI"
if ! already codex "Codex CLI"; then
  ensure_node
  npm install -g @openai/codex
  already codex "Codex CLI" || warn "codex not on PATH — check: npm root -g"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo
say "CLI status:"
any_missing=0
for pair in "multica:multica CLI" "claude:Claude Code" "codex:Codex CLI"; do
  cmd="${pair%%:*}" label="${pair#*:}"
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "$label"
  else
    warn "$label — not found on PATH"
    any_missing=1
  fi
done

echo
say "Next: connect this machine as a runtime"
echo "  Installation does not authenticate providers or prove runtime availability."
echo "  The following interactive setup changes CLI connection/auth configuration and starts a daemon."
echo "  Run it only after reviewing the target and your existing credentials."
# Read ports from lona .env if present (overrides env vars)
_lona_env="$(cd "$(dirname "$0")/.." && pwd)/.env"
_env_val() { grep -E "^$1=" "$_lona_env" 2>/dev/null | head -1 | cut -d= -f2- || true; }
BACKEND_PORT="$(_env_val MULTICA_BACKEND_PORT)"; BACKEND_PORT="${BACKEND_PORT:-8083}"
FRONTEND_PORT="$(_env_val MULTICA_FRONTEND_PORT)"; FRONTEND_PORT="${FRONTEND_PORT:-3000}"
echo "  multica setup self-host \\"
echo "    --server-url http://127.0.0.1:${BACKEND_PORT} \\"
echo "    --app-url    http://127.0.0.1:${FRONTEND_PORT}"
echo
echo "  Then: multica daemon status"

if [ "$any_missing" != "0" ]; then
  echo
  warn "Some CLIs are missing — agents using those providers won't execute tasks."
  exit 1
fi
