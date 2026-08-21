#!/usr/bin/env bash
# T9 — --notify: Telegram DM on state change only; retry on delivery failure.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap 'stub_stop; sandbox_teardown' EXIT
cd "$SANDBOX"
stub_start
export OPENROUTER_BASE_URL="$STUB_URL/api/v1"
export TELEGRAM_API_BASE="$STUB_URL"
export DOCTOR_HTTP_TIMEOUT=2

mkdir -p data/hermes
cat > data/hermes/config.yaml <<'EOF'
model:
  provider: openrouter
  default: alpha/one:free
fallback_model:
  provider: openrouter
  model: beta/two:free
EOF

healthy()  { echo '{"data":[{"id":"alpha/one:free"},{"id":"beta/two:free"}]}' > "$STUB_DIR/models.json"; }
degraded() { echo '{"data":[{"id":"alpha/one:free"}]}' > "$STUB_DIR/models.json"; }
sends() { grep -c 'sendMessage' "$STUB_DIR/requests.log" || true; }
clear_log() { : > "$STUB_DIR/requests.log"; }
STATE=data/doctor-state-hermes

# (0) first run, healthy → no DM, but state recorded
healthy; clear_log
scripts/doctor.sh hermes --notify >/dev/null 2>&1
assert_eq 0 "$(sends)" "(first healthy run: no DM)"
[ -f "$STATE" ] || fail "(state file created on first run)"

# (1) degrades → exactly 1 DM
degraded; clear_log
rc=0; scripts/doctor.sh hermes --notify >/dev/null 2>&1 || rc=$?
assert_eq 1 "$rc" "(degraded exit)"
assert_eq 1 "$(sends)" "(degradation DM sent)"

# (2) identical degraded rerun → no repeat DM
clear_log
scripts/doctor.sh hermes --notify >/dev/null 2>&1 || true
assert_eq 0 "$(sends)" "(no repeat DM on same state)"

# (3) recovers → 1 recovery DM
healthy; clear_log
scripts/doctor.sh hermes --notify >/dev/null 2>&1
assert_eq 1 "$(sends)" "(recovery DM sent)"

# (4) healthy rerun → silent
clear_log
scripts/doctor.sh hermes --notify >/dev/null 2>&1
assert_eq 0 "$(sends)" "(no DM while stable)"

# (5) delivery failure → state not persisted, next run retries
degraded
echo '{"status":500}' > "$STUB_DIR/telegram.json"
prev_state="$(cat "$STATE")"
clear_log
out="$(scripts/doctor.sh hermes --notify 2>&1)" || true
assert_eq 1 "$(sends)" "(failed delivery attempted)"
assert_contains "$out" "notify failed" "(delivery failure warned)"
assert_eq "$prev_state" "$(cat "$STATE")" "(state unchanged on failed delivery)"
echo '{"status":200}' > "$STUB_DIR/telegram.json"
clear_log
scripts/doctor.sh hermes --notify >/dev/null 2>&1 || true
assert_eq 1 "$(sends)" "(retry succeeds next run)"

# (6) Telegram unconfigured → warn, no request, exit still by health
sed -i '/^TELEGRAM_BOT_TOKEN=/d' .env
healthy; clear_log
rc=0; out="$(scripts/doctor.sh hermes --notify 2>&1)" || rc=$?
assert_eq 0 "$rc" "(unconfigured: exit by health)"
assert_eq 0 "$(sends)" "(unconfigured: no request)"
assert_contains "$out" "Telegram not configured" "(unconfigured warning)"

echo "   notify state machine ok"
