#!/usr/bin/env bash
# Provision the Multica host daemon: register boot autostart and install the
# committed tuning drop-in (GC + optional daemon knobs). Idempotent; never
# restarts a running daemon — new values apply on the next daemon start.
#
#   ./deploy.sh multica daemon-setup
#   # or directly:
#   bash scripts/setup-daemon.sh
set -euo pipefail

cd "$(dirname "$0")/.."

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m✓\033[0m  %s\n' "$*"; }

command -v multica >/dev/null 2>&1 \
  || die "multica CLI not found — run ./deploy.sh multica install-agents first."

# systemctl --user needs a reachable user bus; non-login shells often lack it.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
systemctl --user is-system-running >/dev/null 2>&1 \
  || die "systemd --user is not reachable (need linger: sudo loginctl enable-linger $USER)."

say "Registering daemon autostart"
multica daemon autostart enable

unit_dir="$HOME/.config/systemd/user/multica-daemon.service.d"
src="config/multica/multica-daemon.service.d/tuning.conf"
[ -f "$src" ] || die "Missing $src in repo."
mkdir -p "$unit_dir"
# Supersede any ad-hoc drop-in from manual tuning.
rm -f "$unit_dir/gc.conf"
install -m 0644 "$src" "$unit_dir/tuning.conf"
ok "Installed $unit_dir/tuning.conf"

systemctl --user daemon-reload

say "Effective tuning systemd will launch the daemon with:"
systemctl --user show multica-daemon.service -p Environment \
  | tr ' ' '\n' | grep '^MULTICA_' || warn "No MULTICA_* resolved — check the drop-in."

echo
ok "Autostart enabled; tuning persisted."
warn "A running daemon keeps its current env. Apply now with:"
echo "    systemctl --user restart multica-daemon.service"
echo "  (interrupts in-flight runs; they are re-polled). Otherwise it applies on next reboot."
