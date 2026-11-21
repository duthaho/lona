#!/usr/bin/env bash
# T-skills — skills.sh argument handling, bundle listing, and deploy.sh routing.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap sandbox_teardown EXIT
cd "$SANDBOX"

# --- argument validation ---
rc=0; out="$(scripts/skills.sh 2>&1)" || rc=$?
assert_eq 64 "$rc" "(no args)"

rc=0; out="$(scripts/skills.sh openclaw 2>&1)" || rc=$?
assert_eq 64 "$rc" "(non-hermes platform rejected)"

rc=0; out="$(scripts/skills.sh hermes install 2>&1)" || rc=$?
assert_eq 64 "$rc" "(install with no bundle name)"

rc=0; out="$(scripts/skills.sh hermes install nonexistent 2>&1)" || rc=$?
assert_eq 1 "$rc" "(unknown bundle exits 1)"
assert_contains "$out" "not found" "(error message mentions not found)"

# --- list shows all 3 bundles ---
rc=0; out="$(scripts/skills.sh hermes list 2>&1)" || rc=$?
assert_eq 0 "$rc" "(list exits 0)"
assert_contains "$out" "personal-secretary" "(personal-secretary listed)"
assert_contains "$out" "coding"             "(coding listed)"
assert_contains "$out" "homelab"            "(homelab listed)"

# --- deploy.sh routes skills to skills.sh ---
rc=0; out="$(./deploy.sh hermes skills list 2>&1)" || rc=$?
assert_eq 0 "$rc" "(deploy.sh skills list exits 0)"
assert_contains "$out" "personal-secretary" "(deploy.sh routes to skills.sh)"

# --- deploy.sh skills install with no name exits 64 ---
rc=0; ./deploy.sh hermes skills install 2>&1 || rc=$?
assert_eq 64 "$rc" "(deploy.sh skills install no bundle exits 64)"

# --- deploy.sh skills on openclaw exits non-zero ---
rc=0; ./deploy.sh openclaw skills list 2>&1 || rc=$?
[ "$rc" -ne 0 ] || fail "(openclaw skills should exit non-zero)"

echo "   skills ok"
