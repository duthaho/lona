#!/usr/bin/env bash
# selfupdate.sh argument handling: usage + exit 64 on bad invocation.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap sandbox_teardown EXIT
cd "$SANDBOX"

rc=0; out="$(scripts/selfupdate.sh 2>&1)" || rc=$?
assert_eq 64 "$rc" "(no args)"
assert_contains "$out" "selfupdate.sh <openclaw|hermes>" "(no args usage)"

rc=0; out="$(scripts/selfupdate.sh minimax 2>&1)" || rc=$?
assert_eq 64 "$rc" "(unknown platform)"

rc=0; out="$(scripts/selfupdate.sh openclaw --bogus 2>&1)" || rc=$?
assert_eq 64 "$rc" "(unknown flag)"

echo "   update cli arg handling ok"
