#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap sandbox_teardown EXIT
cd "$SANDBOX"
mkdir -p bin
cat > bin/docker <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
if [[ "$*" == *'compose version'* ]]; then exit 0; fi
if [[ "${FAIL_PULL:-0}" == 1 && "$*" == *' pull '* ]]; then exit 42; fi
if [[ "${FAIL_UP:-0}" == 1 && "$*" == *' up '* ]]; then exit 43; fi
exit 0
SH
chmod +x bin/docker
export PATH="$SANDBOX/bin:$PATH" DOCKER_LOG="$SANDBOX/docker.log"
for action in backup cli sync doctor; do
  rc=0; out="$(./deploy.sh multica "$action" 2>&1)" || rc=$?
  [ "$rc" -ne 0 ] || { echo "$action incorrectly succeeded"; exit 1; }
  [[ "$out" != *'unbound variable'* ]] || { echo "$out"; exit 1; }
done
[ ! -d backups ] || { echo 'unsupported backup created archives'; exit 1; }
# All exported Multica secrets must survive a later run without exports.
fixture_key="$(printf '%032d' 0 | base64 | tr -d '\n')"
MULTICA_VCS_SECRET_KEY="$fixture_key" MULTICA_JWT_SECRET='fixture-jwt' MULTICA_POSTGRES_PASSWORD='fixture-First_123' ./deploy.sh multica up >/dev/null
[ "$(grep '^MULTICA_VCS_SECRET_KEY=' .env | cut -d= -f2-)" = "$fixture_key" ] || { echo 'exported VCS key not persisted'; exit 1; }
[ "$(grep '^MULTICA_JWT_SECRET=' .env | cut -d= -f2-)" = 'fixture-jwt' ] || { echo 'exported JWT secret not persisted'; exit 1; }
[ "$(grep '^MULTICA_POSTGRES_PASSWORD=' .env | cut -d= -f2-)" = 'fixture-First_123' ] || { echo 'exported database password not persisted'; exit 1; }
./deploy.sh multica up >/dev/null
[ "$(grep '^MULTICA_VCS_SECRET_KEY=' .env | cut -d= -f2-)" = "$fixture_key" ] || { echo 'VCS key changed on later run'; exit 1; }
[ "$(grep '^MULTICA_JWT_SECRET=' .env | cut -d= -f2-)" = 'fixture-jwt' ] || { echo 'JWT secret changed on later run'; exit 1; }
other_key="$(printf '%032d' 1 | base64 | tr -d '\n')"
for action in up update; do
  : > "$DOCKER_LOG"
  MULTICA_VCS_SECRET_KEY="$other_key" ./deploy.sh multica "$action" >/dev/null 2>&1 && { echo "$action accepted mismatched VCS key"; exit 1; }
  MULTICA_JWT_SECRET='other-jwt' ./deploy.sh multica "$action" >/dev/null 2>&1 && { echo "$action accepted mismatched JWT secret"; exit 1; }
  ! grep -q ' up ' "$DOCKER_LOG" || { echo 'mismatched secret reached compose up'; exit 1; }
  [ "$(grep '^MULTICA_VCS_SECRET_KEY=' .env | cut -d= -f2-)" = "$fixture_key" ] || { echo 'VCS key overwritten'; exit 1; }
  [ "$(grep '^MULTICA_JWT_SECRET=' .env | cut -d= -f2-)" = 'fixture-jwt' ] || { echo 'JWT secret overwritten'; exit 1; }
done
[ "$(grep '^MULTICA_POSTGRES_PASSWORD=' .env | cut -d= -f2-)" = 'fixture-First_123' ] || { echo 'database password changed on later run'; exit 1; }
: > "$DOCKER_LOG"
rc=0; out="$(MULTICA_POSTGRES_PASSWORD='fixture-Different' ./deploy.sh multica up 2>&1)" || rc=$?
[ "$rc" -ne 0 ] && [[ "$out" == *'differs from .env'* ]] || { echo 'mismatched exported database password accepted'; exit 1; }
[ "$(grep '^MULTICA_POSTGRES_PASSWORD=' .env | cut -d= -f2-)" = 'fixture-First_123' ] || { echo 'existing database password overwritten'; exit 1; }
! grep -q ' up ' "$DOCKER_LOG" || { echo 'mismatched database password reached compose up'; exit 1; }
: > "$DOCKER_LOG"
MULTICA_POSTGRES_PASSWORD='fixture-Different' ./deploy.sh multica update >/dev/null 2>&1 && { echo 'update accepted mismatched database password'; exit 1; }
! grep -q ' up ' "$DOCKER_LOG" || { echo 'mismatched database password reached update up'; exit 1; }
: > "$DOCKER_LOG"
rc=0; out="$(MULTICA_POSTGRES_PASSWORD='synthetic@bad' ./deploy.sh multica up 2>&1)" || rc=$?
[ "$rc" -ne 0 ] && [[ "$out" == *MULTICA_POSTGRES_PASSWORD* ]] || { echo 'unsafe database password accepted'; exit 1; }
! grep -q ' up ' "$DOCKER_LOG" || { echo 'unsafe password reached compose up'; exit 1; }
key="$(grep '^MULTICA_VCS_SECRET_KEY=' .env | cut -d= -f2-)"
KEY="$key" python3 -c 'import os,base64; assert len(base64.b64decode(os.environ["KEY"],validate=True)) == 32'
./deploy.sh multica up >/dev/null
[ "$(grep '^MULTICA_VCS_SECRET_KEY=' .env | cut -d= -f2-)" = "$key" ] || { echo 'valid key rotated'; exit 1; }
MULTICA_VCS_SECRET_KEY= ./deploy.sh multica up >/dev/null 2>&1 && { echo 'empty exported key accepted'; exit 1; }
[ "$(grep '^MULTICA_VCS_SECRET_KEY=' .env | cut -d= -f2-)" = "$key" ] || { echo 'empty exported key rotated persisted key'; exit 1; }
sed -i 's/^MULTICA_VCS_SECRET_KEY=.*/MULTICA_VCS_SECRET_KEY=invalid/' .env
rc=0; out="$(./deploy.sh multica up 2>&1)" || rc=$?
[ "$rc" -ne 0 ] && [[ "$out" == *MULTICA_VCS_SECRET_KEY* ]] || { echo 'invalid key accepted'; exit 1; }
grep -q '^MULTICA_VCS_SECRET_KEY=invalid$' .env
sed -i "s|^MULTICA_VCS_SECRET_KEY=.*|MULTICA_VCS_SECRET_KEY=$key|" .env
: > "$DOCKER_LOG"
FAIL_PULL=1 ./deploy.sh multica update >/dev/null 2>&1 && { echo 'update pull failure ignored'; exit 1; }
grep -q ' pull ' "$DOCKER_LOG" || { echo 'update did not attempt pull'; exit 1; }
! grep -q ' up ' "$DOCKER_LOG" || { echo 'up followed failed pull'; exit 1; }
: > "$DOCKER_LOG"
./deploy.sh multica update >/dev/null
grep -q ' up -d --wait' "$DOCKER_LOG" || { echo 'update did not wait'; exit 1; }
FAIL_UP=1 ./deploy.sh multica update >/dev/null 2>&1 && { echo 'update readiness failure ignored'; exit 1; }
EDITOR=true ./deploy.sh multica config >/dev/null
EDITOR=false ./deploy.sh multica config >/dev/null 2>&1 && { echo 'config editor failure ignored'; exit 1; }
# Exercise argument preservation without contacting a Multica server.
cat > config/multica/seed-team.sh <<'SH'
#!/usr/bin/env bash
printf '<%s>\n' "$@"
SH
chmod +x config/multica/seed-team.sh
out="$(./deploy.sh multica team-setup --sync --adopt-existing --runtime 'agent one')"
[ "$out" = "$(printf '<%s>\n' --sync --adopt-existing --runtime 'agent one')" ] || { echo 'team-setup arguments not forwarded'; exit 1; }
# Stub successful installer commands that do not actually install multica.
mkdir -p "$SANDBOX/install-bin"
for cmd in bash dirname grep head cut; do ln -s "$(command -v "$cmd")" "$SANDBOX/install-bin/$cmd"; done
for cmd in brew npm node claude codex; do
  printf '#!/usr/bin/env bash\nexit 0\n' > "$SANDBOX/install-bin/$cmd"
  chmod +x "$SANDBOX/install-bin/$cmd"
done
PATH="$SANDBOX/install-bin" bash scripts/install-coding-agents.sh >/dev/null 2>&1 && { echo 'installer reported success with a missing binary'; exit 1; }
echo '   Multica deployment failure paths ok'
