#!/usr/bin/env bash
# Lona skill bundle installer — installs curated Hermes plugin+skill bundles.
#
#   scripts/skills.sh hermes list
#   scripts/skills.sh hermes install <bundle>
#
# Normally invoked via: ./deploy.sh hermes skills list|install <bundle>
set -euo pipefail

cd "$(dirname "$0")/.."

usage() {
  sed -n '4,7p' "$0" | sed 's/^# \{0,1\}//'
  exit 64
}

PLATFORM="${1:-}"
[ $# -ge 1 ] && shift
case "$PLATFORM" in
  hermes) ;;
  *) usage ;;
esac

ACTION="${1:-list}"
[ $# -ge 1 ] && shift

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

dc() { docker compose --profile "$PLATFORM" "$@"; }

is_running() { [ "$(docker inspect -f '{{.State.Running}}' "lona-$PLATFORM" 2>/dev/null)" = "true" ]; }

BUNDLES_DIR="config/hermes/skills"

list_bundles() {
  echo "Available bundles:"
  local found=0
  for f in "$BUNDLES_DIR"/*.yaml; do
    [ -f "$f" ] || continue
    found=1
    name="$(basename "$f" .yaml)"
    desc="$(python3 -c "
import yaml, sys
d = yaml.safe_load(open('$f')) or {}
print(d.get('description', ''))
" 2>/dev/null || echo "(no description)")"
    printf '  %-24s %s\n' "$name" "$desc"
  done
  [ "$found" = 1 ] || echo "  (none found in $BUNDLES_DIR)"
}

install_bundle() {
  local bundle="$1"
  local f="$BUNDLES_DIR/$bundle.yaml"
  [ -f "$f" ] || die "Bundle '$bundle' not found. Run: ./deploy.sh hermes skills list"

  is_running || die "lona-hermes is not running — start it first: ./deploy.sh hermes up"
  command -v python3 >/dev/null 2>&1 || die "python3 is required for skills install"

  say "Installing bundle: $bundle"

  PLATFORM="$PLATFORM" BUNDLE_FILE="$f" SOUL_FILE="data/hermes/SOUL.md" \
  python3 - <<'PY'
import os, sys, subprocess, textwrap
try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

f        = os.environ["BUNDLE_FILE"]
platform = os.environ["PLATFORM"]
soul_f   = os.environ["SOUL_FILE"]
b        = yaml.safe_load(open(f)) or {}
dc_base  = ["docker", "compose", "--profile", platform]

def say(msg):  print(f"\033[1;36m==>\033[0m {msg}")
def warn(msg): print(f"\033[1;33m!!\033[0m {msg}", file=sys.stderr)

plugins = b.get("plugins") or []
if plugins:
    say(f"Installing plugin(s): {', '.join(plugins)}")
    for p in plugins:
        r = subprocess.run(
            dc_base + ["exec", "-T", "hermes", "hermes", "plugins", "install", p],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            print(f"    plugin: {p}")
        else:
            warn(f"Could not install {p} — {(r.stderr or r.stdout).strip()}")
            warn(f"Run manually: ./deploy.sh hermes cli plugins install {p}")

soul_patch = (b.get("soul_patch") or "").strip()
if soul_patch:
    marker = f"<!-- bundle:{b.get('name', 'unknown')} -->"
    if os.path.exists(soul_f):
        existing = open(soul_f).read()
        if marker not in existing:
            with open(soul_f, "a") as fh:
                fh.write(f"\n\n{marker}\n{textwrap.dedent(soul_patch)}\n")
            say(f"Appended soul_patch to {soul_f}")
        else:
            say("soul_patch already applied — skipping")
    else:
        warn(f"{soul_f} not found — seed config first: ./deploy.sh hermes up")

cron = b.get("cron_suggestions") or []
if cron:
    say("Suggested cron jobs — tell your bot to set these up:")
    for entry in cron:
        print(f"    {entry.get('schedule','?')}  {entry.get('description','')}")

notes = (b.get("notes") or "").strip()
if notes:
    say("Notes:")
    for line in textwrap.dedent(notes).splitlines():
        print(f"  {line}")
PY

  say "Restarting hermes to load changes"
  dc restart hermes
  say "Bundle '$bundle' installed."
}

case "$ACTION" in
  list)
    list_bundles
    ;;
  install)
    [ $# -ge 1 ] || { echo "Usage: ./deploy.sh hermes skills install <bundle>"; exit 64; }
    install_bundle "$1"
    ;;
  *)
    usage
    ;;
esac
