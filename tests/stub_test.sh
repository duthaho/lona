#!/usr/bin/env bash
# T4 — the stub server serves fixtures and logs requests.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap 'stub_stop; sandbox_teardown' EXIT
cd "$SANDBOX"

stub_start
echo '{"data":[{"id":"some/model:free"}]}' > "$STUB_DIR/models.json"

out="$(curl -fsS "$STUB_URL/api/v1/models")"
assert_contains "$out" "some/model:free" "(models fixture served)"

out="$(curl -fsS -X POST "$STUB_URL/api/v1/chat/completions" \
  -d '{"model":"some/model:free","messages":[]}')"

log="$(cat "$STUB_DIR/requests.log")"
assert_contains "$log" "GET /api/v1/models" "(models request logged)"
assert_contains "$log" "POST /api/v1/chat/completions model=some/model:free" \
  "(completion request logged with model)"

echo "   stub server ok"
