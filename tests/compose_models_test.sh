#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Explicitly set COMPOSE_BIN to a standalone compose binary, or use Docker plugin.
if [ -n "${COMPOSE_BIN:-}" ]; then compose=("$COMPOSE_BIN"); else compose=(docker compose); fi
# Use an isolated temporary project: never read the operator's .env.
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp docker-compose*.yml "$tmp"/
: > "$tmp/.env"
cd "$tmp"
unset HERMES_DASHBOARD_PASSWORD HERMES_API_SERVER_KEY COHUB_API_TOKEN MULTICA_POSTGRES_PASSWORD MULTICA_JWT_SECRET MULTICA_VCS_SECRET_KEY DOCKER_MTU COMPOSE_PROJECT_NAME COMPOSE_FILE || true
env -u HERMES_DASHBOARD_PASSWORD -u HERMES_API_SERVER_KEY -u COHUB_API_TOKEN -u MULTICA_POSTGRES_PASSWORD -u MULTICA_JWT_SECRET -u MULTICA_VCS_SECRET_KEY "${compose[@]}" --profile openclaw config -q
HERMES_DASHBOARD_PASSWORD=ci HERMES_API_SERVER_KEY=ci COHUB_API_TOKEN=ci "${compose[@]}" -f docker-compose.yml -f docker-compose.hermes.yml --profile hermes config -q
MULTICA_POSTGRES_PASSWORD=synthetic-._~09 MULTICA_JWT_SECRET=ci MULTICA_VCS_SECRET_KEY=Y2lra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s= "${compose[@]}" -f docker-compose.multica.yml --profile multica config --format json > model.json
python3 - <<'PY'
import json
model = json.load(open('model.json'))
assert model['name'] == 'lona'
assert model['volumes']['multica-pgdata']['name'] == 'lona_multica-pgdata'
assert model['volumes']['multica-uploads']['name'] == 'lona_multica-uploads'
assert model['networks']['default']['driver_opts']['com.docker.network.driver.mtu'] == '1200'
from urllib.parse import urlsplit, unquote
backend = model['services']['multica-backend']['environment']
password = model['services']['multica-postgres']['environment']['POSTGRES_PASSWORD']
assert unquote(urlsplit(backend['DATABASE_URL']).password or '') == password, 'backend and postgres passwords disagree'
# Exercise the real Compose entrypoint guard without a daemon or real secrets.
import os, subprocess
from pathlib import Path
entrypoint = model['services']['multica-backend'].get('entrypoint')
assert entrypoint, 'backend must reject passwords unsafe for a PostgreSQL URI'
# `compose config` re-escapes literal dollar signs for round-tripping YAML.
entrypoint = [arg.replace('$$', '$') for arg in entrypoint]
Path('entrypoint.sh').write_text('#!/bin/sh\nexit 0\n')
Path('entrypoint.sh').chmod(0o755)
for value, expected in [('synthetic-._~09', 0), ('synthetic@bad', 1), ('synthetic%40bad', 1)]:
    result = subprocess.run(entrypoint, env={**os.environ, 'MULTICA_POSTGRES_PASSWORD': value}, capture_output=True)
    assert (result.returncode == 0) == (expected == 0), f'password URI safety guard failed for synthetic fixture {value!r}: {result.returncode}, {result.stderr!r}'
    if expected:
        assert b'MULTICA_POSTGRES_PASSWORD' in result.stderr, 'unsafe password must fail clearly'
PY
if "${compose[@]}" -f docker-compose.multica.yml --profile multica config -q >/dev/null 2>&1; then echo 'missing Multica secrets accepted'; exit 1; fi
echo '   Compose platform isolation ok'
