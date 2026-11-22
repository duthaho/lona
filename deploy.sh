#!/usr/bin/env bash
# Lona — one-command deploy for OpenClaw or Hermes Agent.
#
#   ./deploy.sh openclaw            # deploy OpenClaw (default action: up)
#   ./deploy.sh hermes              # deploy Hermes Agent
#   ./deploy.sh multica             # deploy Multica AI Agent Platform
#   ./deploy.sh <platform> <action> # up|down|restart|logs|status|update|config|sync|backup|doctor|cli|skills
#                                   # multica also: install-agents|team-setup
#
# Examples:
#   ./deploy.sh openclaw logs
#   ./deploy.sh openclaw config              # edit config in $EDITOR, validate + apply
#   ./deploy.sh openclaw cli pairing approve telegram ABC123
#   ./deploy.sh hermes cli setup
#   ./deploy.sh hermes skills list           # list available skill bundles
#   ./deploy.sh hermes skills install <bundle>  # install a bundle
set -euo pipefail

cd "$(dirname "$0")"

PLATFORM="${1:-}"
ACTION="${2:-up}"
[ $# -ge 2 ] && shift 2 || shift $# || true

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

case "$PLATFORM" in
  openclaw|hermes|multica) ;;
  *) usage ;;
esac

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

rand_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

validate_multica_key() {
  local key="${MULTICA_VCS_SECRET_KEY-$(env_val MULTICA_VCS_SECRET_KEY)}"
  command -v python3 >/dev/null 2>&1 || die "Multica key validation requires python3."
  printf '%s' "$key" | python3 -c 'import base64,sys
try:
    value = sys.stdin.buffer.read()
    raw = base64.b64decode(value, validate=True)
    assert len(raw) == 32 and base64.b64encode(raw) == value
except Exception:
    sys.exit(1)' || die "MULTICA_VCS_SECRET_KEY must be canonical base64 of 32 bytes; existing key was not rotated."
}

validate_multica_postgres_password() {
  local password="${MULTICA_POSTGRES_PASSWORD-$(env_val MULTICA_POSTGRES_PASSWORD)}"
  case "$password" in
    ''|*[!a-zA-Z0-9._~-]*) die "MULTICA_POSTGRES_PASSWORD must use only ASCII letters, digits, . _ ~ -; existing password was not changed." ;;
  esac
}

set_env_var() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i.bak "s|^${key}=.*|${key}=${val}|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$key" "$val" >> .env
  fi
}

env_val() {
  grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true
}

require_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  if [ "$(uname -s)" != "Linux" ]; then
    die "Docker (with the compose plugin) is required. Install Docker Desktop and re-run."
  fi
  warn "Docker not found."
  local reply=y
  if [ -t 0 ]; then read -r -p "Install Docker now via get.docker.com? [Y/n] " reply; fi
  case "${reply:-y}" in
    [Yy]*|"") curl -fsSL https://get.docker.com | sh ;;
    *) die "Docker is required. Install it and re-run." ;;
  esac
  if ! docker info >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
      sudo usermod -aG docker "$USER" || true
      die "Docker installed, but your user can't talk to it yet. Log out/in (or run: newgrp docker) and re-run."
    fi
    die "Docker installed but the daemon is not reachable."
  fi
}

ensure_env() {
  if [ ! -f .env ]; then
    say "Creating .env from .env.example"
    cp .env.example .env
    chmod 600 .env
  fi

  if [ -t 0 ]; then
    if [ -z "$(env_val OPENROUTER_API_KEY)" ]; then
      read -r -p "OpenRouter API key (https://openrouter.ai/keys): " v
      [ -n "$v" ] && set_env_var OPENROUTER_API_KEY "$v"
    fi
    if [ -z "$(env_val TELEGRAM_BOT_TOKEN)" ]; then
      read -r -p "Telegram bot token (from @BotFather, blank to skip): " v
      [ -n "$v" ] && set_env_var TELEGRAM_BOT_TOKEN "$v"
    fi
    if [ -z "$(env_val TELEGRAM_ALLOWED_USERS)" ]; then
      read -r -p "Your Telegram user ID (ask @userinfobot, blank to skip): " v
      [ -n "$v" ] && set_env_var TELEGRAM_ALLOWED_USERS "$v"
    fi
    if [ -z "$(env_val GITHUB_TOKEN)" ]; then
      read -r -p "GitHub token (unlocks github skills — github.com/settings/tokens, blank to skip): " v
      [ -n "$v" ] && set_env_var GITHUB_TOKEN "$v"
    fi
    if [ -z "$(env_val GOOGLE_API_KEY)" ]; then
      read -r -p "Google AI Studio key (Gemini free tier — aistudio.google.com, blank to skip): " v
      [ -n "$v" ] && set_env_var GOOGLE_API_KEY "$v"
    fi
  fi

  [ -z "$(env_val OPENCLAW_GATEWAY_TOKEN)" ]    && set_env_var OPENCLAW_GATEWAY_TOKEN "$(rand_hex)"
  [ -z "$(env_val HERMES_DASHBOARD_PASSWORD)" ] && set_env_var HERMES_DASHBOARD_PASSWORD "$(rand_hex)"
  [ -z "$(env_val HERMES_API_SERVER_KEY)" ]     && set_env_var HERMES_API_SERVER_KEY "$(rand_hex)"
  [ -z "$(env_val COHUB_API_TOKEN)" ]           && set_env_var COHUB_API_TOKEN "$(rand_hex)"

  [ "$PLATFORM" = multica ] || [ -n "$(env_val OPENROUTER_API_KEY)" ] \
    || die "OPENROUTER_API_KEY is empty — edit .env and re-run."

  if [ "$PLATFORM" = multica ]; then
    if [ "${MULTICA_VCS_SECRET_KEY+x}" = x ] || [ -n "$(env_val MULTICA_VCS_SECRET_KEY)" ]; then
      validate_multica_key
      if [ "${MULTICA_VCS_SECRET_KEY+x}" = x ]; then
        if [ -z "$(env_val MULTICA_VCS_SECRET_KEY)" ]; then
          set_env_var MULTICA_VCS_SECRET_KEY "$MULTICA_VCS_SECRET_KEY"
        elif [ "$(env_val MULTICA_VCS_SECRET_KEY)" != "$MULTICA_VCS_SECRET_KEY" ]; then
          die "MULTICA_VCS_SECRET_KEY differs from .env; existing key was not changed."
        fi
      fi
    else
      set_env_var MULTICA_VCS_SECRET_KEY "$(head -c 32 /dev/urandom | base64 | tr -d '\n')"
      validate_multica_key
    fi
    if [ "${MULTICA_JWT_SECRET+x}" = x ]; then
      [ -n "$MULTICA_JWT_SECRET" ] || die "MULTICA_JWT_SECRET must not be empty."
      if [ -z "$(env_val MULTICA_JWT_SECRET)" ]; then
        case "$MULTICA_JWT_SECRET" in
          *[!a-zA-Z0-9._~-]*) die "Set MULTICA_JWT_SECRET durably in .env before exporting a value with special characters." ;;
        esac
        set_env_var MULTICA_JWT_SECRET "$MULTICA_JWT_SECRET"
      elif [ "$(env_val MULTICA_JWT_SECRET)" != "$MULTICA_JWT_SECRET" ]; then
        die "MULTICA_JWT_SECRET differs from .env; existing secret was not changed."
      fi
    elif [ -z "$(env_val MULTICA_JWT_SECRET)" ]; then
      set_env_var MULTICA_JWT_SECRET "$(rand_hex)"
    fi
    if [ "${MULTICA_POSTGRES_PASSWORD+x}" = x ]; then
      validate_multica_postgres_password
      if [ -z "$(env_val MULTICA_POSTGRES_PASSWORD)" ]; then
        set_env_var MULTICA_POSTGRES_PASSWORD "$MULTICA_POSTGRES_PASSWORD"
      elif [ "$(env_val MULTICA_POSTGRES_PASSWORD)" != "$MULTICA_POSTGRES_PASSWORD" ]; then
        die "MULTICA_POSTGRES_PASSWORD differs from .env; reconcile the durable database credential before continuing."
      fi
    elif [ -z "$(env_val MULTICA_POSTGRES_PASSWORD)" ]; then
      set_env_var MULTICA_POSTGRES_PASSWORD "$(rand_hex)"
    fi
    validate_multica_postgres_password
  fi
  :
}

seed_config() {
  case "$PLATFORM" in
    multica)
      mkdir -p data/multica
      ;;
    openclaw)
      mkdir -p data/openclaw
      if [ ! -f data/openclaw/openclaw.json ]; then
        say "Seeding data/openclaw/openclaw.json"
        cp config/openclaw/openclaw.json data/openclaw/openclaw.json
      fi
      ;;
    hermes)
      command -v python3 >/dev/null 2>&1 || die "Hermes team seeding requires Python 3 on the host (no PyYAML needed for up)."
      mkdir -p data/hermes data/cohub
      if [ ! -f data/hermes/config.yaml ]; then
        say "Seeding data/hermes/config.yaml"
        cp config/hermes/config.yaml data/hermes/config.yaml
      fi
      if [ ! -f data/hermes/SOUL.md ] && [ -f config/hermes/SOUL.md ]; then
        say "Seeding data/hermes/SOUL.md"
        cp config/hermes/SOUL.md data/hermes/SOUL.md
      fi
      python3 scripts/hermes_team.py seed --templates config/hermes --home data/hermes
      set_env_var HERMES_UID "$(id -u)"
      set_env_var HERMES_GID "$(id -g)"
      ;;
  esac
}

# The gateway reads provider keys (OPENROUTER/GOOGLE) from the compose process
# env; the skill sandbox reads $HERMES_HOME/.env with process-env secrets
# scrubbed — so mirror only skill-consumed secrets (GITHUB_TOKEN) into that file.
propagate_hermes_secrets() {
  [ "$PLATFORM" = hermes ] || return 0
  local dst=data/hermes/.env key val wrote="" i=0
  # First deploy: the gateway writes this file a moment after start.
  while [ ! -f "$dst" ] && [ "$i" -lt 15 ]; do sleep 1; i=$((i + 1)); done
  [ -f "$dst" ] || { touch "$dst"; chmod 600 "$dst"; }
  for key in GITHUB_TOKEN; do
    val="$(env_val "$key")"
    [ -n "$val" ] || continue
    if grep -q "^${key}=" "$dst"; then
      sed -i.bak "s|^${key}=.*|${key}=${val}|" "$dst" && rm -f "$dst.bak"
    else
      printf '%s=%s\n' "$key" "$val" >> "$dst"
    fi
    wrote="$wrote $key"
  done
  [ -n "$wrote" ] && say "Mirrored to data/hermes/.env for skills:$wrote (run /reload in chat to load into the live tool env)"
  return 0
}

reconcile_hermes_api_key() {
  [ "$PLATFORM" = hermes ] || return 0
  local dst=data/hermes/.env persisted lona i=0
  while [ ! -f "$dst" ] && [ "$i" -lt 15 ]; do sleep 1; i=$((i + 1)); done
  persisted="$(grep -E '^API_SERVER_KEY=' "$dst" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
  [ -n "$persisted" ] || return 0
  lona="$(env_val HERMES_API_SERVER_KEY)"
  [ "$persisted" = "$lona" ] && return 0
  set_env_var HERMES_API_SERVER_KEY "$persisted"
  say "Reconciled HERMES_API_SERVER_KEY with Hermes' persisted key so Cohub authenticates"
  is_running && dc up -d --no-deps cohub >/dev/null 2>&1 || true
  return 0
}

# Inject TELEGRAM_GROUP_ALLOWED_CHATS into OpenClaw's group allowlist (idempotent).
sync_openclaw_groups() {
  local f="${LONA_TARGET:-data/openclaw/openclaw.json}" ids id
  ids="$(env_val TELEGRAM_GROUP_ALLOWED_CHATS | tr -d ' ')"
  [ -n "$ids" ] && [ -f "$f" ] || return 0
  if ! grep -q '__LONA_GROUPS__' "$f"; then
    warn "No __LONA_GROUPS__ marker in $f — manage the group allowlist manually."
    return 0
  fi
  local IFS=,
  for id in $ids; do
    grep -q "\"$id\"" "$f" && continue
    awk -v id="$id" '
      !done && /__LONA_GROUPS__/ {
        match($0, /^[ \t]*/)
        print substr($0, 1, RLENGTH) "\"" id "\": { requireMention: true },"
        done = 1
      }
      { print }
    ' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    say "Allowlisted Telegram group $id in openclaw.json (@mention required)"
  done
}

# Inject TELEGRAM_ALLOWED_USERS into OpenClaw's DM allowlist + owner, and flip
# dmPolicy pairing→allowlist. Idempotent.
sync_openclaw_users() {
  local f="${LONA_TARGET:-data/openclaw/openclaw.json}" ids id first=""
  ids="$(env_val TELEGRAM_ALLOWED_USERS | tr -d ' ')"
  [ -n "$ids" ] && [ -f "$f" ] || return 0
  if ! grep -q '__LONA_ALLOWFROM__' "$f"; then
    warn "No __LONA_ALLOWFROM__ marker in $f — manage the DM allowlist manually."
    return 0
  fi
  local IFS=,
  for id in $ids; do
    [ -n "$first" ] || first="$id"
    grep -q "\"telegram:$id\"" "$f" && continue
    awk -v id="$id" '
      !done && /__LONA_ALLOWFROM__/ {
        match($0, /^[ \t]*/)
        print substr($0, 1, RLENGTH) "\"telegram:" id "\","
        done = 1
      }
      { print }
    ' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    say "Allowlisted Telegram user $id for DMs in openclaw.json"
  done
  if grep -q '__LONA_OWNER__' "$f" \
    && ! grep -B1 '__LONA_OWNER__' "$f" | grep -q '"telegram:'; then
    awk -v id="$first" '
      !done && /__LONA_OWNER__/ {
        match($0, /^[ \t]*/)
        print substr($0, 1, RLENGTH) "\"telegram:" id "\","
        done = 1
      }
      { print }
    ' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    say "Set Telegram user $first as command owner in openclaw.json"
  fi
  if grep -q 'dmPolicy: "pairing"' "$f"; then
    sed -i.bak 's|dmPolicy: "pairing"|dmPolicy: "allowlist"|' "$f" && rm -f "$f.bak"
    say "Switched OpenClaw DM policy to allowlist (ids from TELEGRAM_ALLOWED_USERS)"
  fi
}

dc() {
  case "$PLATFORM" in
    multica) docker compose -f docker-compose.multica.yml --profile "$PLATFORM" "$@" ;;
    hermes) docker compose -f docker-compose.yml -f docker-compose.hermes.yml --profile "$PLATFORM" "$@" ;;
    *) docker compose --profile "$PLATFORM" "$@" ;;
  esac
}

_running_container() {
  case "$PLATFORM" in
    multica) echo "lona-multica-backend" ;;
    *)       echo "lona-$PLATFORM" ;;
  esac
}
is_running() { [ "$(docker inspect -f '{{.State.Running}}' "$(_running_container)" 2>/dev/null)" = "true" ]; }

# Model-chain listing check (free ids rotate); warns, never blocks the deploy.
doctor_quick() {
  [ "$PLATFORM" = multica ] && return 0
  if ! scripts/doctor.sh "$PLATFORM" --quick; then
    warn "Model chain degraded — run: ./deploy.sh $PLATFORM doctor"
  fi
}

# Install rtk-rewrite (token compression) and jev-skill-router (skill routing)
# on first deploy. Non-fatal: warns if the registry is unreachable.
install_hermes_core_plugins() {
  [ "$PLATFORM" = hermes ] || return 0
  local i=0
  while ! dc exec -T hermes true 2>/dev/null && [ $i -lt 15 ]; do
    sleep 1; i=$((i + 1))
  done
  is_running || return 0
  say "Installing core plugins (rtk-rewrite, jev-skill-router)"
  for plugin in rtk-rewrite jev-skill-router; do
    out="$(dc exec -T hermes hermes plugins install "$plugin" 2>&1)" && continue
    echo "$out" | grep -q "already exists" && continue
    warn "Could not install $plugin — run: ./deploy.sh hermes cli plugins install $plugin"
  done
}

# Kanban databases are runtime state, never committed. Recreate the empty
# enterprise board on each new volume, without changing the current board.
ensure_hermes_team_board() {
  [ "$PLATFORM" = hermes ] || return 0
  is_running || { warn "Hermes not running — enterprise-apps board not created yet"; return 0; }
  local boards
  boards="$(dc exec -T hermes hermes kanban boards list --json 2>/dev/null)" \
    || { warn "Cannot list Kanban boards — check Hermes image compatibility"; return 0; }
  if printf '%s' "$boards" | python3 -c \
    'import json,sys; sys.exit(0 if any(b.get("slug") == "enterprise-apps" for b in json.load(sys.stdin)) else 1)' \
    2>/dev/null; then return 0; fi
  dc exec -T hermes hermes kanban boards create enterprise-apps \
    --name "Enterprise Applications" \
    --description "Product, architecture, implementation, data, QA, security, SRE and independent review" \
    || warn "Could not create enterprise-apps board — retry after Hermes is healthy"
}

# OpenClaw's built-in audit tightens perms + flips risky policies. Non-fatal.
audit_openclaw() {
  [ "$PLATFORM" = openclaw ] || return 0
  say "Running OpenClaw security audit"
  dc run --rm --no-deps --entrypoint node openclaw dist/index.js security audit --fix \
    || warn "Security audit reported findings — review the output above."
}

next_steps() {
  echo
  say "Done. $PLATFORM is running."
  case "$PLATFORM" in
    multica)
      _mfe="$(env_val MULTICA_FRONTEND_PORT)"; _mfe="${_mfe:-3000}"
      _mbe="$(env_val MULTICA_BACKEND_PORT)";  _mbe="${_mbe:-8083}"
      echo "  • Status:    ./deploy.sh multica status"
      echo "  • Logs:      ./deploy.sh multica logs"
      echo "  • Frontend:  ssh -L ${_mfe}:127.0.0.1:${_mfe} <user>@<vps>  →  http://127.0.0.1:${_mfe}"
      echo "  • Backend:   ssh -L ${_mbe}:127.0.0.1:${_mbe} <user>@<vps>  →  http://127.0.0.1:${_mbe}"
      echo
      echo "  First login (no email configured):"
      echo "    ./deploy.sh multica logs | grep -i 'verification code'"
      echo
      echo "  Install coding agent CLIs on this machine:"
      echo "    ./deploy.sh multica install-agents"
      echo
      echo "  Seed the enterprise agent team (run after creating your account + workspace):"
      echo "    ./deploy.sh multica team-setup"
      ;;
    openclaw)
      echo "  • Status:    ./deploy.sh openclaw status"
      echo "  • Logs:      ./deploy.sh openclaw logs"
      echo "  • Dashboard: ssh -L 18789:127.0.0.1:18789 <user>@<vps>  →  http://127.0.0.1:18789"
      echo "               (token: OPENCLAW_GATEWAY_TOKEN in .env)"
      if [ -n "$(env_val TELEGRAM_BOT_TOKEN)" ]; then
        if [ -n "$(env_val TELEGRAM_ALLOWED_USERS)" ]; then
          echo "  • Telegram:  DM your bot — ids in TELEGRAM_ALLOWED_USERS are allowlisted."
        else
          echo "  • Telegram:  DM your bot, get a pairing code, then:"
          echo "               ./deploy.sh openclaw cli pairing approve telegram <CODE>"
          echo "               (tip: set TELEGRAM_ALLOWED_USERS in .env for the sturdier allowlist)"
        fi
        echo "  • Groups:    add bot to group → copy chat id from logs →"
        echo "               set TELEGRAM_GROUP_ALLOWED_CHATS in .env → ./deploy.sh openclaw up"
      fi
      ;;
    hermes)
      echo "  • Status:    ./deploy.sh hermes status"
      echo "  • Logs:      ./deploy.sh hermes logs"
      echo "  • Dashboard: ssh -L 9119:127.0.0.1:9119 <user>@<vps>  →  http://127.0.0.1:9119"
      echo "               (user/pass: HERMES_DASHBOARD_USER / HERMES_DASHBOARD_PASSWORD in .env)"
      echo "  • Cohub:     ssh -L 8765:127.0.0.1:8765 <user>@<vps>  →  http://127.0.0.1:8765"
      echo "               (token: COHUB_API_TOKEN in .env)"
      if [ -n "$(env_val TELEGRAM_BOT_TOKEN)" ]; then
        echo "  • Telegram:  DM your bot — only IDs in TELEGRAM_ALLOWED_USERS are accepted."
        echo "  • Groups:    add bot to group → copy chat id from logs →"
        echo "               set TELEGRAM_GROUP_ALLOWED_CHATS in .env → ./deploy.sh hermes up"
      fi
      ;;
  esac
}

case "$ACTION" in
  up)
    require_docker
    ensure_env
    seed_config
    if [ "$PLATFORM" = openclaw ]; then
      sync_openclaw_users
      sync_openclaw_groups
    fi
    say "Pulling image + starting $PLATFORM"
    if [ "$PLATFORM" = multica ]; then
      dc pull multica-postgres multica-backend multica-frontend
      dc up -d
    elif [ "$PLATFORM" = hermes ]; then
      dc pull "$PLATFORM"
      dc up -d --build
    else
      dc pull "$PLATFORM"
      dc up -d
    fi
    propagate_hermes_secrets
    reconcile_hermes_api_key
    install_hermes_core_plugins
    ensure_hermes_team_board
    doctor_quick
    audit_openclaw
    next_steps
    ;;
  down)     dc down ;;
  restart)  dc restart ;;
  logs)     dc logs -f --tail=200 ;;
  status)   dc ps ;;
  update)
    if [ "$PLATFORM" = multica ]; then
      [ "$#" -eq 0 ] || die "Multica update does not support scheduling or arguments."
      validate_multica_key
      validate_multica_postgres_password
      if [ "${MULTICA_VCS_SECRET_KEY+x}" = x ] && [ "$(env_val MULTICA_VCS_SECRET_KEY)" != "$MULTICA_VCS_SECRET_KEY" ]; then
        die "MULTICA_VCS_SECRET_KEY differs from .env; existing key was not changed."
      fi
      if [ "${MULTICA_JWT_SECRET+x}" = x ] && [ "$(env_val MULTICA_JWT_SECRET)" != "$MULTICA_JWT_SECRET" ]; then
        die "MULTICA_JWT_SECRET differs from .env; existing secret was not changed."
      fi
      [ -n "${MULTICA_JWT_SECRET-$(env_val MULTICA_JWT_SECRET)}" ] || die "MULTICA_JWT_SECRET must not be empty."
      if [ "${MULTICA_POSTGRES_PASSWORD+x}" = x ] && [ "$(env_val MULTICA_POSTGRES_PASSWORD)" != "$MULTICA_POSTGRES_PASSWORD" ]; then
        die "MULTICA_POSTGRES_PASSWORD differs from .env; reconcile the durable database credential before continuing."
      fi
      warn "Multica update is nontransactional: no automatic backup or rollback."
      dc pull multica-postgres multica-backend multica-frontend
      dc up -d --wait
      exit 0
    fi
    # Transactional: backup → pull → recreate → canary → auto-rollback.
    # `update install`/`uninstall` (in "$@") schedule it via host cron.
    exec scripts/selfupdate.sh "$PLATFORM" "$@"
    ;;
  doctor)
    [ "$PLATFORM" = multica ] && die "Multica doctor is not available; inspect status and logs manually."
    exec scripts/doctor.sh "$PLATFORM" "$@"
    ;;
  config)
    if [ "$PLATFORM" = multica ]; then
      [ -f .env ] || die "No .env found; run ./deploy.sh multica up first."
      "${EDITOR:-nano}" .env
      say "Configuration edited; run ./deploy.sh multica up to apply."
      exit 0
    fi
    case "$PLATFORM" in
      openclaw) f=data/openclaw/openclaw.json ;;
      hermes)   f=data/hermes/config.yaml ;;
    esac
    [ -f "$f" ] || seed_config
    "${EDITOR:-nano}" "$f"
    case "$PLATFORM" in
      hermes)
        if command -v python3 >/dev/null 2>&1; then
          python3 -c "
try:
    import yaml; yaml.safe_load(open('$f'))
except ImportError:
    pass
except Exception as e:
    import sys; print(str(e), file=sys.stderr); raise SystemExit(1)
" || die "config.yaml has invalid YAML syntax — fix it and re-run 'config'. Container NOT restarted."
        fi
        ;;
      openclaw)
        if command -v node >/dev/null 2>&1; then
          npx --yes json5 "$f" >/dev/null 2>&1 \
            || die "openclaw.json has invalid JSON5 — fix it and re-run 'config'. Container NOT restarted."
        fi
        ;;
    esac
    say "Applying config (restarting $PLATFORM)"
    dc restart
    ;;
  sync)
    [ "$PLATFORM" != multica ] || die "Multica sync is unsupported; use ./deploy.sh multica team-setup for explicit team reconciliation."
    # Template → live, backed up first. Hermes deep-merges (keeps live-only
    # runtime keys like home_channel); OpenClaw regenerates + re-injects .env
    # allowlists (drops runtime-only pairing approvals — keep them in .env).
    case "$PLATFORM" in
      hermes)   live_f=data/hermes/config.yaml ;;
      openclaw) live_f=data/openclaw/openclaw.json ;;
    esac
    had_live=0; [ -f "$live_f" ] && had_live=1
    seed_config
    mkdir -p backups
    ts="$(date +%Y%m%d-%H%M%S)"
    case "$PLATFORM" in
      hermes)
        command -v python3 >/dev/null 2>&1 || die "sync needs python3 (with PyYAML) on the host."
        f=data/hermes/config.yaml
        cp "$f" "backups/hermes-config-$ts.yaml"
        say "Backed up live config → backups/hermes-config-$ts.yaml"
        TEMPLATE=config/hermes/config.yaml LIVE="$f" python3 - <<'PY' || die "Merge failed — live config left unchanged."
import os, yaml
tmpl = yaml.safe_load(open(os.environ['TEMPLATE'])) or {}
live = yaml.safe_load(open(os.environ['LIVE'])) or {}
def merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v
    return base
live_only = sorted(set(live) - set(tmpl))
route_snapshot = {
    'profile_routes': live.get('profile_routes'),
    'gateway_routes': (live.get('gateway') or {}).get('profile_routes'),
}
merge(live, tmpl)
# Preserve live Telegram topic ownership; checked-in routes are empty by design.
if route_snapshot['profile_routes']:
    live['profile_routes'] = route_snapshot['profile_routes']
if route_snapshot['gateway_routes']:
    live.setdefault('gateway', {})['profile_routes'] = route_snapshot['gateway_routes']
tmp = os.environ['LIVE'] + '.tmp'
with open(tmp, 'w') as fh:
    yaml.safe_dump(live, fh, sort_keys=False, allow_unicode=True)
os.replace(tmp, os.environ['LIVE'])
print('   template keys applied:', ', '.join(sorted(tmpl)))
print('   live-only keys preserved:', ', '.join(live_only) or '(none)')
PY
        if [ -f config/hermes/SOUL.md ] && ! cmp -s config/hermes/SOUL.md data/hermes/SOUL.md; then
          [ ! -f data/hermes/SOUL.md ] || cp data/hermes/SOUL.md "backups/hermes-SOUL-$ts.md"
          cp config/hermes/SOUL.md data/hermes/SOUL.md
          say "Applied orchestrator SOUL (prior copy in backups/ if it existed)"
        fi
        python3 scripts/hermes_team.py sync --templates config/hermes --home data/hermes \
          || die "Enterprise team sync failed — inspect team assets before restarting."
        propagate_hermes_secrets
        if is_running; then
          say "Applying merged config (restarting hermes)"
          dc restart
          ensure_hermes_team_board
          say "Sync complete. Session-scoped settings (toolsets) apply on the next /reset."
        else
          warn "hermes container not running — config staged. Start it with: ./deploy.sh hermes up"
        fi
        ;;
      openclaw)
        f=data/openclaw/openclaw.json
        [ "$had_live" = 1 ] && { cp "$f" "backups/openclaw-config-$ts.json"; say "Backed up live config → backups/openclaw-config-$ts.json"; }
        # Stage → inject → validate → atomic swap: live is untouched until good.
        stg="$f.stg.$$"
        cp config/openclaw/openclaw.json "$stg"
        LONA_TARGET="$stg" sync_openclaw_users
        LONA_TARGET="$stg" sync_openclaw_groups
        if command -v node >/dev/null 2>&1; then
          npx --yes json5 "$stg" >/dev/null 2>&1 || { rm -f "$stg"; die "sync produced invalid JSON5 — live config left unchanged."; }
        else
          warn "node not found — skipping JSON5 pre-check; the gateway validates on reload."
        fi
        mv "$stg" "$f"
        if is_running; then
          say "Applied template + .env allowlists. OpenClaw hot-reloads (hybrid) — no restart needed."
          say "Confirm it took effect: ./deploy.sh openclaw cli config get channels.telegram.streaming.mode"
        else
          warn "openclaw container not running — config staged. Start it with: ./deploy.sh openclaw up"
        fi
        ;;
    esac
    ;;
  backup)
    [ "$PLATFORM" != multica ] || die "Multica backup is unsupported: PostgreSQL and upload volumes are not backed up by this command."
    mkdir -p backups
    f="backups/${PLATFORM}-$(date +%Y%m%d-%H%M%S).tar.gz"
    paths=("data/$PLATFORM")
    [ "$PLATFORM" = hermes ] && [ -d data/cohub ] && paths+=("data/cohub")
    say "Backing up ${paths[*]} → $f"
    # Exit 1 = "file changed as we read it" — expected while the gateway is
    # live (SQLite WAL churn); the archive is still usable. Exit ≥2 is fatal.
    rc=0
    tar czf "$f" --warning=no-file-changed "${paths[@]}" || rc=$?
    [ "$rc" -le 1 ] || die "Backup failed (tar exit $rc)"
    say "Backup written: $f"
    ;;
  cli)
    [ "$PLATFORM" != multica ] || die "Multica cli proxy is unsupported; use the host multica CLI directly."
    case "$PLATFORM" in
      openclaw) dc run --rm --no-deps --entrypoint node openclaw dist/index.js "$@" ;;
      hermes)   dc run --rm hermes "$@" ;;
    esac
    ;;
  skills)
    [ "$PLATFORM" = hermes ] || die "'skills' is only supported for the hermes platform"
    exec scripts/skills.sh "$PLATFORM" "$@"
    ;;
  install-agents)
    [ "$PLATFORM" = multica ] || die "'install-agents' is only supported for the multica platform"
    exec scripts/install-coding-agents.sh
    ;;
  team-setup)
    [ "$PLATFORM" = multica ] || die "'team-setup' is only supported for the multica platform"
    exec config/multica/seed-team.sh "$@"
    ;;
  *) usage ;;
esac
