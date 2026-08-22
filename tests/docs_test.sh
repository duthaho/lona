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

grep -q 'UPDATE_CRON_SCHEDULE' .env.example \
  || fail "(.env.example documents UPDATE_CRON_SCHEDULE)"
grep -q 'UPDATE_CANARY_WAIT' .env.example \
  || fail "(.env.example documents UPDATE_CANARY_WAIT)"
grep -qi 'Safe updates' README.md \
  || fail "(README documents transactional Safe updates)"
grep -q 'update install' README.md \
  || fail "(README documents update install)"
grep -q 'update uninstall' README.md \
  || fail "(README documents update uninstall)"

echo "   docs coverage ok"
