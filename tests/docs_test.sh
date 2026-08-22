#!/usr/bin/env bash
# T13 — docs mention the doctor action and its config knob.
source "$(dirname "$0")/helpers.sh"
cd "$REPO_ROOT"

grep -q 'DOCTOR_CRON_SCHEDULE' .env.example \
  || fail "(.env.example documents DOCTOR_CRON_SCHEDULE)"
grep -q '`doctor`' README.md \
  || fail "(README documents the doctor action)"
grep -q 'doctor install' README.md \
  || fail "(README documents doctor install)"
grep -q 'doctor uninstall' README.md \
  || fail "(README documents doctor uninstall)"

echo "   docs coverage ok"
