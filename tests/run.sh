#!/usr/bin/env bash
# Lona test suite — runs every tests/*_test.sh; nonzero exit on any failure.
set -u
cd "$(dirname "$0")/.."
pass=0 fail=0
for t in tests/*_test.sh; do
  echo "== $t"
  if bash "$t"; then
    pass=$((pass + 1))
  else
    echo "   FAILED: $t"
    fail=$((fail + 1))
  fi
done
echo "----"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
