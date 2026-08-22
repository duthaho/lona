#!/usr/bin/env bash
# Transactional self-update: no-op, success, canary→rollback, first-deploy,
# and Telegram notify-on-outcome. Driven by the stateful fake docker shim.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
fake_docker_setup
trap 'stub_stop; sandbox_teardown' EXIT
cd "$SANDBOX"
stub_start
export OPENROUTER_BASE_URL="$STUB_URL/api/v1"
export TELEGRAM_API_BASE="$STUB_URL"
echo '{"data":[]}' > "$STUB_DIR/models.json"   # post-update quick check: warn-only
export DOCTOR_HTTP_TIMEOUT=2 UPDATE_HTTP_TIMEOUT=2 UPDATE_CANARY_WAIT=2 UPDATE_CANARY_INTERVAL=1

reset()      { rm -f "$DOCKER_STATE"/*; }
put()        { printf '%s' "$2" > "$DOCKER_STATE/$1"; }
logged()     { grep -c "$1" "$DOCKER_STATE/cmd.log" 2>/dev/null || true; }
cmdlog()     { cat "$DOCKER_STATE/cmd.log" 2>/dev/null || true; }
img()        { cat "$DOCKER_STATE/container_image" 2>/dev/null || true; }
sends()      { grep -c 'sendMessage' "$STUB_DIR/requests.log" 2>/dev/null || true; }
clearsends() { : > "$STUB_DIR/requests.log"; }

# (1) no-op — running image already matches the freshly-pulled one
reset
put container_image sha256:AAA
put tag_id sha256:AAA
rc=0; scripts/selfupdate.sh hermes >/dev/null 2>&1 || rc=$?
assert_eq 0 "$rc" "(no-op exit 0)"
assert_eq 1 "$(logged 'compose pull')" "(no-op still pulls)"
assert_eq 0 "$(logged 'compose up')" "(no-op does not recreate)"

# (2) success — new image, hermes has no healthcheck (running == healthy)
reset
put container_image sha256:OLD
put pull_id sha256:NEW
put status running
put health ''      # real Hermes: no healthcheck → docker emits empty/err here
mkdir -p data/hermes; echo pre-update > data/hermes/state.txt
rc=0; scripts/selfupdate.sh hermes >/dev/null 2>&1 || rc=$?
assert_eq 0 "$rc" "(update success exit 0)"
assert_eq sha256:NEW "$(img)" "(container ends on the new image)"
assert_eq 1 "$(logged 'compose up')" "(recreated once)"
assert_eq 0 "$(logged 'tag ')" "(no rollback tag on success)"

# (3) success — openclaw healthcheck goes healthy; audit hook fires
reset
put container_image sha256:OLD
put pull_id sha256:NEW
put status running
put health healthy
mkdir -p data/openclaw; echo x > data/openclaw/state.txt
rc=0; scripts/selfupdate.sh openclaw >/dev/null 2>&1 || rc=$?
assert_eq 0 "$rc" "(openclaw update success)"
assert_eq sha256:NEW "$(img)" "(openclaw on new image)"
assert_contains "$(cmdlog)" "compose run" "(security audit hook ran)"

# (4) canary failure → rollback restores previous image AND data
reset
put container_image sha256:OLD
put pull_id sha256:NEW
put status restarting                       # crash-loop: canary fails fast
mkdir -p data/hermes; echo pre-update > data/hermes/state.txt
touch "$DOCKER_STATE/corrupt_on_up"         # the bad image corrupts state on up
rc=0; scripts/selfupdate.sh hermes >/dev/null 2>&1 || rc=$?
assert_eq 4 "$rc" "(rollback exit 4)"
assert_eq sha256:OLD "$(img)" "(container rolled back to old image)"
assert_eq pre-update "$(cat data/hermes/state.txt)" "(data restored from pre-update backup)"
assert_contains "$(cmdlog)" "tag sha256:OLD" "(old image retagged onto the ref)"

# (5) first-ever deploy — no baseline → plain update, no canary/rollback
reset
rm -rf data/hermes
put pull_id sha256:NEW
rc=0; scripts/selfupdate.sh hermes >/dev/null 2>&1 || rc=$?
assert_eq 0 "$rc" "(first deploy exit 0)"
assert_eq 1 "$(logged 'compose up')" "(first deploy recreates)"
assert_eq 0 "$(logged 'tag ')" "(no rollback on first deploy)"

# (6) --notify: DM on success
reset; clearsends
put container_image sha256:OLD; put pull_id sha256:NEW; put status running; put health healthy
mkdir -p data/openclaw
scripts/selfupdate.sh openclaw --notify >/dev/null 2>&1 || true
assert_eq 1 "$(sends)" "(success DM sent with --notify)"

# (7) --notify: DM on rollback
reset; clearsends
put container_image sha256:OLD; put pull_id sha256:NEW; put status restarting
mkdir -p data/hermes; echo p > data/hermes/state.txt
rc=0; scripts/selfupdate.sh hermes --notify >/dev/null 2>&1 || rc=$?
assert_eq 4 "$rc" "(rollback exit)"
assert_eq 1 "$(sends)" "(rollback DM sent)"

# (8) --notify: silent on a no-op (scheduled runs must not spam)
reset; clearsends
put container_image sha256:AAA; put tag_id sha256:AAA
scripts/selfupdate.sh hermes --notify >/dev/null 2>&1 || true
assert_eq 0 "$(sends)" "(no DM on no-op)"

# (9) --notify with Telegram unconfigured → warn, no request, exit by outcome
reset; clearsends
put container_image sha256:OLD; put pull_id sha256:NEW; put status running; put health healthy
mkdir -p data/hermes
cp .env "$SANDBOX/.env.keep"
sed -i '/^TELEGRAM_BOT_TOKEN=/d' .env
rc=0; out="$(scripts/selfupdate.sh hermes --notify 2>&1)" || rc=$?
assert_eq 0 "$rc" "(unconfigured: exit by outcome)"
assert_eq 0 "$(sends)" "(unconfigured: no Telegram request)"
assert_contains "$out" "Telegram not configured" "(unconfigured warning)"
mv "$SANDBOX/.env.keep" .env

# (10) rollback itself fails (retag errors) → exit 5, not a false exit 4
reset; clearsends
put container_image sha256:OLD; put pull_id sha256:NEW; put status restarting
put tag_fail 1
mkdir -p data/hermes; echo p > data/hermes/state.txt
rc=0; scripts/selfupdate.sh hermes --notify >/dev/null 2>&1 || rc=$?
assert_eq 5 "$rc" "(rollback failure exits 5)"
assert_eq 1 "$(sends)" "(rollback-failure DM sent)"

# (11) backup fails on a baselined update → abort BEFORE recreate, exit 1,
#      nothing changed (no false "rolled back"). Force tar to fail by making
#      backups/ a regular file so the archive can't be written.
reset
put container_image sha256:OLD; put pull_id sha256:NEW; put status running; put health ''
mkdir -p data/hermes; echo keep > data/hermes/state.txt
rm -rf backups; : > backups          # backups is now a file → tar can't write
rc=0; scripts/selfupdate.sh hermes >/dev/null 2>&1 || rc=$?
assert_eq 1 "$rc" "(backup failure aborts, exit 1)"
assert_eq 0 "$(logged 'compose up')" "(aborted before recreate — container untouched)"
rm -f backups

echo "   transactional update state machine ok"
