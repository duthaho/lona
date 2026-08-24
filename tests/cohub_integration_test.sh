#!/usr/bin/env bash
# Cohub integration — deployment wiring, generated secrets, and Hermes backups.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap 'sandbox_teardown' EXIT
cp "$REPO_ROOT/docker-compose.yml" "$SANDBOX/"
cp "$REPO_ROOT/.env.example" "$SANDBOX/.env.example"
cp "$REPO_ROOT/README.md" "$SANDBOX/README.md"
mkdir -p "$SANDBOX/apps"
cp -r "$REPO_ROOT/apps/cohub" "$SANDBOX/apps/cohub"
cd "$SANDBOX"

[ -f apps/cohub/Dockerfile ] || fail "(Cohub Dockerfile exists)"
grep -q '^  cohub:' docker-compose.yml || fail "(Compose defines Cohub service)"
grep -q 'apps/cohub' docker-compose.yml || fail "(Compose builds Cohub from apps/cohub)"
grep -q 'http://hermes:8642' docker-compose.yml || fail "(Cohub uses internal Hermes Runs API)"
grep -q 'data/cohub' docker-compose.yml || fail "(Compose persists Cohub data)"
grep -q 'COHUB_API_TOKEN' .env.example || fail "(.env.example documents Cohub API token)"
grep -q 'HERMES_API_SERVER_KEY' .env.example || fail "(.env.example documents Hermes API server key)"

# Exercise non-interactive Hermes deployment with a fake Docker CLI. This must
# generate both internal API credentials without printing their values.
cat > .env <<'EOF'
OPENROUTER_API_KEY=test-openrouter
HERMES_DASHBOARD_PASSWORD=test-dashboard
EOF
fake_bin="$(dirname "$SANDBOX")/fake-bin"
mkdir -p "$fake_bin"
cat > "$fake_bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_CALLS:?}"
exit 0
EOF
chmod +x "$fake_bin/docker"
cat > scripts/doctor.sh <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x scripts/doctor.sh
export DOCKER_CALLS="$(dirname "$SANDBOX")/docker-calls.log"
rc=0
out="$(PATH="$fake_bin:$PATH" ./deploy.sh hermes up 2>&1)" || rc=$?
assert_eq 0 "$rc" "(Hermes deployment with Cohub wiring: $out)"
cohub_token="$(grep '^COHUB_API_TOKEN=' .env | cut -d= -f2-)"
hermes_key="$(grep '^HERMES_API_SERVER_KEY=' .env | cut -d= -f2-)"
[ "${#cohub_token}" -ge 32 ] || fail "(deploy generates strong COHUB_API_TOKEN)"
[ "${#hermes_key}" -ge 32 ] || fail "(deploy generates strong HERMES_API_SERVER_KEY)"

# A Hermes backup must include Cohub because the two services form one personal
# coworker installation. Runtime state remains git-ignored.
mkdir -p data/hermes data/cohub
printf 'hermes\n' > data/hermes/state.txt
printf 'cohub\n' > data/cohub/state.txt
./deploy.sh hermes backup >/dev/null
archive="$(find backups -name 'hermes-*.tar.gz' | head -1)"
[ -n "$archive" ] || fail "(Hermes backup archive created)"
listing="$(tar tzf "$archive")"
assert_contains "$listing" 'data/hermes/state.txt' "(backup contains Hermes state)"
assert_contains "$listing" 'data/cohub/state.txt' "(backup contains Cohub state)"

grep -q 'Cohub' README.md || fail "(README documents Cohub)"
grep -q '8765' README.md || fail "(README documents Cohub dashboard port)"

echo "   Cohub integration ok"
