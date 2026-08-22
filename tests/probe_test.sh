#!/usr/bin/env bash
# T5+ — probe tiers, statuses, exit codes (stubbed OpenRouter).
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap 'stub_stop; sandbox_teardown' EXIT
cd "$SANDBOX"
stub_start
export OPENROUTER_BASE_URL="$STUB_URL/api/v1"
export DOCTOR_HTTP_TIMEOUT=2
export DOCTOR_PROBE_BACKOFF=0   # don't sleep between retries in tests

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

# ---- T6: completion status mappings on the primary ----
listing_all
t6_case() { # stub-status expected-status expected-rc
  echo "{\"alpha/one:free\": $1}" > "$STUB_DIR/completions.json"
  clear_log
  rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
  assert_eq "$3" "$rc" "(completion $1 exit)"
  line="$(printf '%s\n' "$out" | grep 'alpha/one:free (completion)')"
  assert_contains "$line" "$2" "(completion $1 status)"
}
t6_case 429 LIMITED 2
t6_case 404 DEAD 2
t6_case 400 DEAD 2
t6_case 500 ERROR 2
t6_case '"timeout"' ERROR 2
rm "$STUB_DIR/completions.json"

# default mode → exactly 1 completion call
clear_log
scripts/doctor.sh hermes >/dev/null 2>&1
n="$(grep -c 'chat/completions' "$STUB_DIR/requests.log" || true)"
assert_eq 1 "$n" "(default mode: one completion)"

# --deep → one completion per chain member
clear_log
scripts/doctor.sh hermes --deep >/dev/null 2>&1
n="$(grep -c 'chat/completions' "$STUB_DIR/requests.log" || true)"
assert_eq 3 "$n" "(--deep: completions for all)"

# --quick → zero completions; still healthy via listing
clear_log
rc=0; scripts/doctor.sh hermes --quick >/dev/null 2>&1 || rc=$?
assert_eq 0 "$rc" "(--quick healthy exit)"
n="$(grep -c 'chat/completions' "$STUB_DIR/requests.log" || true)"
assert_eq 0 "$n" "(--quick: no completions)"

echo "   completion probe + modes ok"

# ---- T7: gemini primary + openrouter fallback ----
cat > data/hermes/config.yaml <<'EOF'
model:
  provider: gemini
  default: gemini-test
fallback_model:
  provider: openrouter
  model: beta/two:free
EOF
echo '{"data":[{"id":"beta/two:free"}]}' > "$STUB_DIR/models.json"
export GEMINI_BASE_URL="$STUB_URL"
echo 'GOOGLE_API_KEY=g-test-key' >> .env

g_case() { # stub-status expected-status expected-rc
  echo "{\"gemini-test\": $1}" > "$STUB_DIR/gemini.json"
  clear_log
  rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
  assert_eq "$3" "$rc" "(gemini $1 exit)"
  line="$(printf '%s\n' "$out" | grep 'gemini-test (completion)')"
  assert_contains "$line" "$2" "(gemini $1 status)"
}
g_case 200 OK 0
g_case 429 LIMITED 2
g_case '"timeout"' ERROR 2
rm "$STUB_DIR/gemini.json"

# --quick → no gemini request, SKIP status, healthy exit
clear_log
rc=0; out="$(scripts/doctor.sh hermes --quick 2>&1)" || rc=$?
assert_eq 0 "$rc" "(gemini --quick exit)"
assert_contains "$out" "SKIP" "(gemini --quick skipped)"
n="$(grep -c 'generateContent' "$STUB_DIR/requests.log" || true)"
assert_eq 0 "$n" "(gemini --quick: no request)"

# no GOOGLE_API_KEY → ERROR without any request
sed -i '/^GOOGLE_API_KEY=/d' .env
clear_log
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 2 "$rc" "(gemini keyless exit)"
assert_contains "$out" "no GOOGLE_API_KEY" "(gemini keyless reason)"
n="$(grep -c 'generateContent' "$STUB_DIR/requests.log" || true)"
assert_eq 0 "$n" "(gemini keyless: no request)"

echo "   gemini probe ok"

# ---- T8: no probe run may modify platform config [D1] ----
mkdir -p data/openclaw
cp config/openclaw/openclaw.json data/openclaw/openclaw.json
listing_all
before="$(data_checksums)"
scripts/doctor.sh hermes >/dev/null 2>&1 || true
scripts/doctor.sh hermes --deep >/dev/null 2>&1 || true
scripts/doctor.sh openclaw --quick >/dev/null 2>&1 || true
after="$(data_checksums)"
assert_eq "$before" "$after" "(configs untouched by probes)"

echo "   config immutability ok"

# ---- T12: --quiet is silent when healthy, loud when degraded ----
cat > data/hermes/config.yaml <<'EOF'
model:
  provider: openrouter
  default: alpha/one:free
fallback_model:
  provider: openrouter
  model: beta/two:free
EOF
echo '{"data":[{"id":"alpha/one:free"},{"id":"beta/two:free"}]}' > "$STUB_DIR/models.json"
rc=0; out="$(scripts/doctor.sh hermes --quiet 2>&1)" || rc=$?
assert_eq 0 "$rc" "(quiet healthy exit)"
assert_eq "" "$out" "(quiet healthy: no output)"

echo '{"data":[{"id":"alpha/one:free"}]}' > "$STUB_DIR/models.json"
rc=0; out="$(scripts/doctor.sh hermes --quiet 2>&1)" || rc=$?
assert_eq 1 "$rc" "(quiet degraded exit)"
assert_contains "$out" "DEAD" "(quiet degraded: report printed)"

echo "   quiet mode ok"

# ---- Review-fix B: subscription-only config (no free models) → nothing to
# probe is not a failure; exit 0 so post-deploy check never false-alarms ----
cat > data/hermes/config.yaml <<'EOF'
model:
  provider: openai
  default: gpt-5.3-codex-spark
EOF
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 0 "$rc" "(no free models: exit 0)"
assert_contains "$out" "no free models" "(no free models: clear note)"
n="$(grep -c 'chat/completions' "$STUB_DIR/requests.log" || true)"

echo "   no-free-model handling ok"

# ---- Retry: a transient ERROR on the primary is retried once (smooths the
# false "primary unusable" from a one-off timeout/5xx) ----
cat > data/hermes/config.yaml <<'EOF'
model:
  provider: openrouter
  default: alpha/one:free
fallback_model:
  provider: openrouter
  model: beta/two:free
EOF
listing_all() {
  echo '{"data":[{"id":"alpha/one:free"},{"id":"beta/two:free"},{"id":"gamma/three:free"}]}' \
    > "$STUB_DIR/models.json"
}
listing_all

# fail once (500) then succeed → OK, and exactly 2 completion attempts
echo '{"alpha/one:free": [500, 200]}' > "$STUB_DIR/completions.json"
clear_log; curl -fsS "$STUB_URL/__reset" >/dev/null
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 0 "$rc" "(transient ERROR retried → healthy)"
line="$(printf '%s\n' "$out" | grep 'alpha/one:free (completion)')"
assert_contains "$line" "OK" "(retry recovers to OK)"
n="$(grep -c 'model=alpha/one:free' "$STUB_DIR/requests.log" || true)"
assert_eq 2 "$n" "(one retry = two attempts)"

# persistent ERROR → still ERROR after the retry, 2 attempts
echo '{"alpha/one:free": [500, 500]}' > "$STUB_DIR/completions.json"
clear_log
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 2 "$rc" "(persistent ERROR stays exit 2)"
n="$(grep -c 'model=alpha/one:free' "$STUB_DIR/requests.log" || true)"
assert_eq 2 "$n" "(persistent ERROR: capped at two attempts)"

# retries disabled → single attempt
echo '{"alpha/one:free": [500, 200]}' > "$STUB_DIR/completions.json"
clear_log; curl -fsS "$STUB_URL/__reset" >/dev/null
rc=0; DOCTOR_PROBE_RETRIES=0 scripts/doctor.sh hermes >/dev/null 2>&1 || rc=$?
assert_eq 2 "$rc" "(retries=0 → no recovery)"
n="$(grep -c 'model=alpha/one:free' "$STUB_DIR/requests.log" || true)"
assert_eq 1 "$n" "(retries=0: one attempt)"

# LIMITED (429) is a real signal — NOT retried
echo '{"alpha/one:free": 429}' > "$STUB_DIR/completions.json"
clear_log
rc=0; out="$(scripts/doctor.sh hermes 2>&1)" || rc=$?
assert_eq 2 "$rc" "(429 exit 2)"
line="$(printf '%s\n' "$out" | grep 'alpha/one:free (completion)')"
assert_contains "$line" "LIMITED" "(429 stays LIMITED)"
n="$(grep -c 'model=alpha/one:free' "$STUB_DIR/requests.log" || true)"
assert_eq 1 "$n" "(429 not retried)"
rm "$STUB_DIR/completions.json"

echo "   probe retry ok"
