#!/usr/bin/env bash
# Lona bootstrap — zero-to-assistant on a fresh Ubuntu/Debian VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/duthaho/lona/main/scripts/bootstrap.sh | bash -s -- openclaw
#   curl -fsSL https://raw.githubusercontent.com/duthaho/lona/main/scripts/bootstrap.sh | bash -s -- hermes
#
# Override the repo with: LONA_REPO=https://github.com/you/lona.git
set -euo pipefail

PLATFORM="${1:-openclaw}"
REPO_URL="${LONA_REPO:-https://github.com/duthaho/lona.git}"
DEST="${LONA_DIR:-$HOME/lona}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  say "Installing git + curl"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y && sudo apt-get install -y git curl
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y git curl
  else
    echo "Install git and curl manually, then re-run." >&2; exit 1
  fi
fi

if [ -d "$DEST/.git" ]; then
  say "Updating existing checkout at $DEST"
  git -C "$DEST" pull --ff-only
else
  say "Cloning $REPO_URL → $DEST"
  git clone "$REPO_URL" "$DEST"
fi

cd "$DEST"
exec ./deploy.sh "$PLATFORM"
