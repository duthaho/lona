#!/usr/bin/env bash
# Idempotent cron install/uninstall for scheduled self-update, space-safe path.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap sandbox_teardown EXIT

FAKE_DIR="$(dirname "$SANDBOX")"
export FAKE_CRON_FILE="$FAKE_DIR/cronfile"
cat > "$FAKE_DIR/fakecrontab" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-l" ]; then cat "$FAKE_CRON_FILE" 2>/dev/null; else cat > "$FAKE_CRON_FILE"; fi
EOF
chmod +x "$FAKE_DIR/fakecrontab"
export CRONTAB_CMD="$FAKE_DIR/fakecrontab"

cd "$SANDBOX"
entries() { grep -c 'lona-update' "$FAKE_CRON_FILE" 2>/dev/null || true; }

scripts/selfupdate.sh hermes install >/dev/null
assert_eq 1 "$(entries)" "(install once)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "cd '$SANDBOX'" "(space-safe quoted path)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "0 4 * * *" "(default schedule)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "update --notify --quiet" "(cron flags)"

scripts/selfupdate.sh hermes install >/dev/null
assert_eq 1 "$(entries)" "(install idempotent)"

DOCTOR_CRON_SCHEDULE=ignored UPDATE_CRON_SCHEDULE="*/30 * * * *" \
  scripts/selfupdate.sh hermes install >/dev/null
assert_eq 1 "$(entries)" "(reinstall replaces)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "*/30 * * * *" "(custom schedule)"

scripts/selfupdate.sh openclaw install >/dev/null
assert_eq 2 "$(entries)" "(two platforms coexist)"

scripts/selfupdate.sh hermes uninstall >/dev/null
assert_eq 1 "$(entries)" "(uninstall hermes only)"
assert_contains "$(cat "$FAKE_CRON_FILE")" "lona-update-openclaw" "(openclaw survives)"

scripts/selfupdate.sh hermes uninstall >/dev/null
assert_eq 1 "$(entries)" "(uninstall idempotent)"

scripts/selfupdate.sh openclaw uninstall >/dev/null
assert_eq 0 "$(entries)" "(all removed)"

echo "   update cron install/uninstall ok"
