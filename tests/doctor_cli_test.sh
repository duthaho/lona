#!/usr/bin/env bash
# T1 — doctor.sh argument handling: usage + exit 64 on bad invocation.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap sandbox_teardown EXIT
cd "$SANDBOX"

rc=0; out="$(scripts/doctor.sh 2>&1)" || rc=$?
assert_eq 64 "$rc" "(no args)"
assert_contains "$out" "doctor.sh <openclaw|hermes>" "(no args usage)"

rc=0; out="$(scripts/doctor.sh minimax 2>&1)" || rc=$?
assert_eq 64 "$rc" "(unknown platform)"

rc=0; out="$(scripts/doctor.sh openclaw --bogus 2>&1)" || rc=$?
assert_eq 64 "$rc" "(unknown flag)"

echo "   cli arg handling ok"
