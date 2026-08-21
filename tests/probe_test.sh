#!/usr/bin/env bash
# T5+ — probe tiers, statuses, exit codes (stubbed OpenRouter).
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap 'stub_stop; sandbox_teardown' EXIT
cd "$SANDBOX"
stub_start
export OPENROUTER_BASE_URL="$STUB_URL/api/v1"
export DOCTOR_HTTP_TIMEOUT=2

# Controlled 3-model hermes chain: alpha (primary), beta, gamma.
mkdir -p data/hermes
cat > data/hermes/config.yaml <<'EOF'
model:
  provider: openrouter
  default: alpha/one:free
fallback_model:
  provider: openrouter
  model: beta/two:free
auxiliary:
  openrouter_model: gamma/three:free
EOF

listing_all() {
  echo '{"data":[{"id":"alpha/one:free"},{"id":"beta/two:free"},{"id":"gamma/three:free"}]}' \
    > "$STUB_DIR/models.json"
}
clear_log() { : > "$STUB_DIR/requests.log"; }

# (a) all ids listed, primary completion 200 → healthy, exit 0
listing_all; clear_log
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 0 "$rc" "(healthy exit)"
assert_contains "$out" "OK" "(healthy statuses)"
assert_contains "$out" "beta/two:free (listing)" "(fallback via listing tier)"
assert_contains "$out" "alpha/one:free (completion)" "(primary via completion tier)"

# (b) one fallback missing from listing → DEAD, exit 1, no completion call for it
echo '{"data":[{"id":"alpha/one:free"},{"id":"gamma/three:free"}]}' > "$STUB_DIR/models.json"
clear_log
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 1 "$rc" "(degraded exit)"
assert_contains "$out" "DEAD" "(dead fallback status)"
n="$(grep -c 'model=beta/two:free' "$STUB_DIR/requests.log" || true)"
assert_eq 0 "$n" "(no completion probe for dead id)"

# (c) primary missing from listing → DEAD primary, exit 2, no completion at all
echo '{"data":[{"id":"beta/two:free"},{"id":"gamma/three:free"}]}' > "$STUB_DIR/models.json"
clear_log
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 2 "$rc" "(primary unusable exit)"
n="$(grep -c 'chat/completions' "$STUB_DIR/requests.log" || true)"
assert_eq 0 "$n" "(dead primary not completion-probed)"

# (e) listing endpoint down → warn, fallbacks ERROR (listing), primary still
# completion-probed; healthy primary + ERROR fallbacks → exit 1
echo '{"__status__":500}' > "$STUB_DIR/models.json"
clear_log
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 1 "$rc" "(listing-down exit)"
assert_contains "$out" "listing unavailable" "(listing-down warning)"
assert_contains "$out" "ERROR" "(listing-down fallback status)"
n="$(grep -c 'model=alpha/one:free' "$STUB_DIR/requests.log" || true)"
assert_eq 1 "$n" "(primary still probed when listing down)"

echo "   listing probe + exit codes ok"
