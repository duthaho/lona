#!/usr/bin/env bash
# T10 — idempotent cron install/uninstall via $CRONTAB_CMD, space-safe path.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap sandbox_teardown EXIT

# Fake crontab: -l lists, "-" replaces from stdin. Lives on a space-free
# path; the SANDBOX itself contains a space (the hard case for the entry).
FAKE_DIR="$(dirname "$SANDBOX")"
export FAKE_CRON_FILE="$FAKE_DIR/cronfile"
cat > "$FAKE_DIR/fakecrontab" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-l" ]; then cat "$FAKE_CRON_FILE" 2>/dev/null; else cat > "$FAKE_CRON_FILE"; fi
EOF
chmod +x "$FAKE_DIR/fakecrontab"
export CRONTAB_CMD="$FAKE_DIR/fakecrontab"

cd "$SANDBOX"
entries() { grep -c 'lona-doctor' "$FAKE_CRON_FILE" 2>/dev/null || true; }

scripts/doctor.sh hermes install >/dev/null
assert_eq 1 "$(entries)" "(install once)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "cd '$SANDBOX'" "(space-safe quoted path)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "0 */6 * * *" "(default schedule)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "doctor --notify --quiet" "(cron flags)"

scripts/doctor.sh hermes install >/dev/null
assert_eq 1 "$(entries)" "(install idempotent)"

DOCTOR_CRON_SCHEDULE="*/30 * * * *" scripts/doctor.sh hermes install >/dev/null
assert_eq 1 "$(entries)" "(reinstall replaces)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "*/30 * * * *" "(custom schedule)"

scripts/doctor.sh openclaw install >/dev/null
assert_eq 2 "$(entries)" "(two platforms coexist)"

scripts/doctor.sh hermes uninstall >/dev/null
assert_eq 1 "$(entries)" "(uninstall hermes only)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "lona-doctor-openclaw" "(openclaw survives)"

scripts/doctor.sh hermes uninstall >/dev/null
assert_eq 1 "$(entries)" "(uninstall idempotent)"

scripts/doctor.sh openclaw uninstall >/dev/null
assert_eq 0 "$(entries)" "(all removed)"

# Review-fix E: a repo path containing a single quote must not break the
# installed cron line (single-quote escaping).
QUOTED_DIR="$(dirname "$SANDBOX")/od'd"
cp -r "$SANDBOX" "$QUOTED_DIR"
( cd "$QUOTED_DIR" && scripts/doctor.sh hermes install >/dev/null )
line="$(grep 'lona-doctor-hermes' "$FAKE_CRON_FILE")"
# The quoted cd target must round-trip through sh back to the real path.
cdpart="${line#* cd }"; cdpart="${cdpart%% && *}"
recovered="$(sh -c "printf %s $cdpart")"
assert_eq "$QUOTED_DIR" "$recovered" "(cron path round-trips through sh with a quote)"

echo "   cron install/uninstall ok"
