#!/usr/bin/env bash
# T11 — deploy.sh: doctor action dispatch + non-fatal --quick hook in up.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap 'stub_stop; sandbox_teardown' EXIT
cd "$SANDBOX"
stub_start
export OPENROUTER_BASE_URL="$STUB_URL/api/v1"
export DOCTOR_HTTP_TIMEOUT=2

healthy() {
  cat > "$STUB_DIR/models.json" <<'EOF'
{"data":[{"id":"nvidia/nemotron-3-ultra-550b-a55b:free"},{"id":"z-ai/glm-5.2:free"},{"id":"nvidia/nemotron-3-super-120b-a12b:free"},{"id":"google/gemma-4-31b-it:free"}]}
EOF
}
degraded() { echo '{"data":[]}' > "$STUB_DIR/models.json"; }

# doctor action dispatches and forwards the exit code
healthy
rc=0; ./deploy.sh openclaw doctor --quick >/dev/null 2>&1 || rc=$?
assert_eq 0 "$rc" "(deploy doctor healthy)"
degraded
rc=0; ./deploy.sh openclaw doctor --quick >/dev/null 2>&1 || rc=$?
assert_eq 2 "$rc" "(deploy doctor exit forwarded)"

# behavioral: `up` with a degraded chain still completes, warning printed
FAKE_DIR="$(dirname "$SANDBOX")/bin"
mkdir -p "$FAKE_DIR"
printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKE_DIR/docker"
chmod +x "$FAKE_DIR/docker"

rc=0; out="$(PATH="$FAKE_DIR:$PATH" ./deploy.sh openclaw up 2>&1)" || rc=$?
assert_eq 0 "$rc" "(up completes despite degraded chain)"
assert_contains "$out" "Model chain degraded" "(up warns on degraded chain)"

# and a healthy chain produces no warning
healthy
rc=0; out="$(PATH="$FAKE_DIR:$PATH" ./deploy.sh openclaw up 2>&1)" || rc=$?
assert_eq 0 "$rc" "(up healthy)"
case "$out" in *"Model chain degraded"*) fail "(unexpected degradation warning)";; esac

# update runs the hook too
degraded
rc=0; out="$(PATH="$FAKE_DIR:$PATH" ./deploy.sh openclaw update 2>&1)" || rc=$?
assert_eq 0 "$rc" "(update completes despite degraded chain)"
assert_contains "$out" "Model chain degraded" "(update warns on degraded chain)"

echo "   deploy.sh wiring ok"
