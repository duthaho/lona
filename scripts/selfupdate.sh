#!/usr/bin/env bash
# Lona transactional self-update — an all-or-nothing image update.
#
#   scripts/selfupdate.sh <openclaw|hermes> [--notify] [--quiet]
#   scripts/selfupdate.sh <openclaw|hermes> install|uninstall
#
# Normally invoked via: ./deploy.sh <platform> update [flags]
# Flow: snapshot (image id + data backup) → pull → recreate → canary → on
# failure, roll back to the previous image and data. Exit codes: 0 updated or
# already-current · 4 rolled back after a failed canary · 5 rollback itself
# failed (needs a human) · 64 usage error.
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

ACTION=update NOTIFY=0 QUIET=0
for arg in "$@"; do
  case "$arg" in
    --notify)  NOTIFY=1 ;;
    --quiet)   QUIET=1 ;;
    install)   ACTION=install ;;
    uninstall) ACTION=uninstall ;;
    *) usage ;;
  esac
done

# Progress/diagnostics go to stderr so command-substitution helpers
# (make_backup, *_id) keep stdout clean for the value they return.
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

short() { local s="${1#sha256:}"; printf '%s' "${s:0:12}"; }

dc() { docker compose --profile "$PLATFORM" "$@"; }

# Resolve the image reference exactly as compose does: shell env → .env →
# the compose default. Rollback retags the saved old image id onto this ref.
image_ref() {
  local v key def
  case "$PLATFORM" in
    openclaw) key=OPENCLAW_IMAGE def="ghcr.io/openclaw/openclaw:latest" ;;
    hermes)   key=HERMES_IMAGE   def="nousresearch/hermes-agent:latest" ;;
  esac
  v="$(printf '%s' "${!key:-}")"
  [ -n "$v" ] || v="$(env_val "$key")"
  [ -n "$v" ] || v="$def"
  printf '%s' "$v"
}

container_image_id() { docker inspect --format '{{.Image}}' "$1" 2>/dev/null || true; }
image_tag_id()       { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || true; }
container_status()   { docker inspect --format '{{.State.Status}}' "$1" 2>/dev/null || true; }
container_health()   { docker inspect --format '{{.State.Health.Status}}' "$1" 2>/dev/null || true; }

# Snapshot data/<platform> before recreating, so a failed canary can restore
# the exact pre-update state (a bad image may migrate on-disk state, so the
# old image needs old-schema data). On success prints the archive path and
# returns 0; when there is no data yet (first deploy) prints nothing and
# returns 0 (nothing to protect); on a real archiving failure returns non-zero
# so the caller can abort before touching the container — an update we can't
# snapshot is an update we can't safely roll back. tar exit 1 = "file changed
# while read" (SQLite WAL churn on a live gateway) — expected, archive usable.
make_backup() {
  [ -d "data/$PLATFORM" ] || return 0
  mkdir -p backups || { warn "Cannot create backups/ — no snapshot taken."; return 1; }
  local f="backups/${PLATFORM}-$(date +%Y%m%d-%H%M%S)-preupdate.tar.gz" rc=0
  tar czf "$f" --warning=no-file-changed "data/$PLATFORM" 2>/dev/null || rc=$?
  if [ "$rc" -le 1 ]; then
    printf '%s' "$f"
    return 0
  fi
  warn "Pre-update backup failed (tar exit $rc)."
  return 1
}

# Restore data/<platform> from a pre-update archive. Extract to a scratch dir
# and swap in only after a verified-good extraction, so live data is never
# destroyed by a corrupt/partial archive. Returns non-zero if a backup was
# named but could not be restored — the caller escalates that to a failed
# rollback (needs a human). An empty archive arg (no data existed at snapshot)
# is benign: nothing to restore, success.
restore_backup() { # archive
  local b="${1:-}"
  if [ -z "$b" ]; then
    warn "No pre-update backup was taken — data left as-is."
    return 0
  fi
  [ -f "$b" ] || { warn "Backup $b missing — live data left untouched."; return 1; }
  local tmp="data/.$PLATFORM.restore.$$"
  rm -rf "$tmp"; mkdir -p "$tmp"
  # Archive stores paths as data/<platform>/… (created from the repo root).
  if ! tar xzf "$b" -C "$tmp" 2>/dev/null || [ ! -d "$tmp/data/$PLATFORM" ]; then
    warn "Data restore from $b failed — live data left untouched."
    rm -rf "$tmp"
    return 1
  fi
  rm -rf "data/$PLATFORM"
  mv "$tmp/data/$PLATFORM" "data/$PLATFORM"
  rm -rf "$tmp"
}

# Canary: the new container must prove itself healthy. Success is `running`
# and (healthy, if the service defines a healthcheck; OpenClaw does, Hermes
# does not) sustained until UPDATE_CANARY_WAIT. A restarting/exited/dead
# state or an `unhealthy` verdict fails fast. This is the "can't brick the
# bot" guarantee.
canary_ok() { # container-name
  local cname="$1" status health waited=0
  local wait="${UPDATE_CANARY_WAIT:-120}" interval="${UPDATE_CANARY_INTERVAL:-5}"
  while :; do
    status="$(container_status "$cname")"
    case "$status" in
      running)
        health="$(container_health "$cname")"
        case "$health" in
          # up & (healthy | no healthcheck). A service without a healthcheck
          # yields empty/`<no value>` (older Docker) or a suppressed template
          # error → empty (newer Docker: "map has no entry for key Health").
          healthy|''|'<no value>'|none) return 0 ;;
          unhealthy) return 1 ;;
          *) ;;  # starting / other transient — keep waiting
        esac ;;
      restarting|exited|dead) return 1 ;;  # crash-looping / crashed
      *) ;;  # created / paused / unknown / empty — keep waiting
    esac
    [ "$waited" -lt "$wait" ] || return 1
    sleep "$interval"; waited=$((waited + interval))
  done
}

# Post-update parity with the plain `up` flow: warn-only chain check + the
# OpenClaw security audit. Never fatal.
post_update_hooks() {
  scripts/doctor.sh "$PLATFORM" --quick >/dev/null 2>&1 \
    || warn "Model chain degraded — run: ./deploy.sh $PLATFORM doctor"
  if [ "$PLATFORM" = openclaw ]; then
    say "Running OpenClaw security audit"
    dc run --rm --no-deps --entrypoint node openclaw dist/index.js security audit --fix \
      || warn "Security audit reported findings — review the output above."
  fi
}

# ---- Telegram notification -------------------------------------------------
# DM the owner on a state-changing outcome only (updated / rolled back);
# no-ops and first-deploy updates stay silent, so scheduled runs never spam.
TELEGRAM_API_BASE="${TELEGRAM_API_BASE:-https://api.telegram.org}"

notify() { # outcome old_id new_id
  local outcome="$1" old_id="$2" new_id="$3" token chat_id msg code
  token="$(env_val TELEGRAM_BOT_TOKEN)"
  chat_id="$(env_val TELEGRAM_ALLOWED_USERS | tr -d ' ' | cut -d, -f1)"
  if [ -z "$token" ] || [ -z "$chat_id" ]; then
    warn "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USERS) — cannot notify."
    return 0
  fi
  case "$outcome" in
    updated)      msg="✅ Lona update ($PLATFORM): updated $(short "$old_id") → $(short "$new_id") — canary passed." ;;
    rolledback)   msg="⚠️ Lona update ($PLATFORM): new image failed its health check — rolled back to $(short "$old_id"). Check: ./deploy.sh $PLATFORM logs" ;;
    rollbackfail) msg="🚨 Lona update ($PLATFORM): update failed AND rollback failed — manual intervention needed. Check: ./deploy.sh $PLATFORM logs" ;;
    *) return 0 ;;
  esac
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "${UPDATE_HTTP_TIMEOUT:-15}" \
    -X POST "$TELEGRAM_API_BASE/bot$token/sendMessage" \
    --data-urlencode "chat_id=$chat_id" --data-urlencode "text=$msg" \
    || echo 000)"
  [ "$code" = 200 ] || warn "Telegram notify failed (HTTP $code)."
}

rollback() { # ref old_id backup
  local ref="$1" old_id="$2" backup="$3"
  dc stop >/dev/null 2>&1 || true
  restore_backup "$backup" || return 1
  docker tag "$old_id" "$ref" || return 1
  dc up -d || return 1
  return 0
}

run_update() {
  local ref cname old_id new_id backup=""
  ref="$(image_ref)"
  cname="lona-$PLATFORM"
  old_id="$(container_image_id "$cname")"   # empty on a first-ever deploy

  say "Pulling latest image for $PLATFORM"
  dc pull >/dev/null || die "Image pull failed for $PLATFORM — nothing changed."
  new_id="$(image_tag_id "$ref")"

  # No-op: the running container already runs the freshly-pulled image.
  if [ -n "$old_id" ] && [ -n "$new_id" ] && [ "$old_id" = "$new_id" ]; then
    [ "$QUIET" = 1 ] || say "$PLATFORM already up to date ($(short "$old_id"))."
    return 0
  fi

  # No rollback baseline (nothing was running) → plain update, no canary.
  if [ -z "$old_id" ]; then
    say "Recreating $PLATFORM"
    dc up -d || die "Recreate failed for $PLATFORM (no previous image to roll back to)."
    say "$PLATFORM updated (no previous image to canary against)."
    post_update_hooks
    return 0
  fi

  # Snapshot before touching the container. If it can't be taken, abort now
  # (nothing changed) rather than start a transaction we couldn't roll back.
  if ! backup="$(make_backup)"; then
    die "Pre-update backup failed — aborting before recreate (rollback not guaranteed)."
  fi
  say "Recreating $PLATFORM"
  # A failed recreate must not fall through to the canary (the old container
  # could still be up and pass it) — treat it like a failed canary.
  if dc up -d && canary_ok "$cname"; then
    say "$PLATFORM updated: $(short "$old_id") → $(short "$new_id") (canary passed)."
    post_update_hooks
    [ "$NOTIFY" = 1 ] && notify updated "$old_id" "$new_id"
    return 0
  fi

  warn "$PLATFORM update did not come up healthy — rolling back to $(short "$old_id")."
  if rollback "$ref" "$old_id" "$backup"; then
    warn "$PLATFORM rolled back to the previous image."
    [ "$NOTIFY" = 1 ] && notify rolledback "$old_id" "$new_id"
    return 4
  fi
  warn "$PLATFORM rollback FAILED — manual intervention needed."
  [ "$NOTIFY" = 1 ] && notify rollbackfail "$old_id" "$new_id"
  return 5
}

# ---- Scheduled runs via host cron ------------------------------------------
CRONTAB_CMD="${CRONTAB_CMD:-crontab}"

cron_schedule() {
  local s="${UPDATE_CRON_SCHEDULE:-}"
  [ -n "$s" ] || s="$(env_val UPDATE_CRON_SCHEDULE)"
  [ -n "$s" ] || s="0 4 * * *"
  printf '%s' "$s"
}

cron_install() {
  local marker="# lona-update-$PLATFORM" entry current qpwd
  # Single-quote the repo path so spaces survive sh parsing, escaping any
  # embedded single quote via the '\'' idiom.
  qpwd="'$(printf '%s' "$PWD" | sed "s/'/'\\\\''/g")'"
  entry="$(cron_schedule) cd $qpwd && ./deploy.sh $PLATFORM update --notify --quiet $marker"
  current="$("$CRONTAB_CMD" -l 2>/dev/null | grep -vF "$marker" || true)"
  printf '%s\n%s\n' "$current" "$entry" | sed '/^$/d' | "$CRONTAB_CMD" -
  say "Installed cron entry: $entry"
}

cron_uninstall() {
  local marker="# lona-update-$PLATFORM" current
  current="$("$CRONTAB_CMD" -l 2>/dev/null | grep -vF "$marker" || true)"
  printf '%s\n' "$current" | sed '/^$/d' | "$CRONTAB_CMD" -
  say "Removed cron entry for $PLATFORM update (if present)."
}

case "$ACTION" in
  install)   cron_install ;;
  uninstall) cron_uninstall ;;
  update)    rc=0; run_update || rc=$?; exit "$rc" ;;
esac
