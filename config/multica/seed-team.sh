#!/usr/bin/env bash
# Seed the Multica enterprise agent team from config/multica/team.json.
#
#   ./deploy.sh multica team-setup
#   # or with an existing token:
#   MULTICA_API_TOKEN=mul_... ./deploy.sh multica team-setup
set -euo pipefail

cd "$(dirname "$0")/../.."

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m✓\033[0m  %s\n' "$*"; }

# Read ports from lona .env (overrides env vars)
_env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
_p="$(_env_val MULTICA_BACKEND_PORT)";  BACKEND_PORT="${_p:-${MULTICA_BACKEND_PORT:-8083}}"
_p="$(_env_val MULTICA_FRONTEND_PORT)"; FRONTEND_PORT="${_p:-${MULTICA_FRONTEND_PORT:-3000}}"
TEAM_JSON="config/multica/team.json"
BASE="http://127.0.0.1:${BACKEND_PORT}"

[ -f "$TEAM_JSON" ] || die "Team config not found: $TEAM_JSON"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v wget    >/dev/null 2>&1 || die "wget is required"

# http_get  URL AUTH_HEADER_LINE  → prints response body
http_get() {
  local url="$1" auth="$2"
  wget -qO- --header="$auth" "$url" 2>/dev/null
}

# http_post URL AUTH_HEADER_LINE PAYLOAD  → prints HTTP status code
http_post() {
  local url="$1" auth="$2" payload="$3"
  python3 - "$url" "$auth" "$payload" <<'PY'
import sys, urllib.request, urllib.error
url, auth_hdr, payload = sys.argv[1], sys.argv[2], sys.argv[3]
k, v = auth_hdr.split(": ", 1)
req = urllib.request.Request(
    url, data=payload.encode(),
    headers={"Content-Type": "application/json", k: v},
    method="POST")
try:
    resp = urllib.request.urlopen(req)
    print(resp.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("000")
PY
}

# ── Wait for backend ──────────────────────────────────────────────────────────
say "Waiting for backend at $BASE/api/config"
i=0
until wget -qO- "$BASE/api/config" >/dev/null 2>&1; do
  i=$((i+1))
  [ $i -lt 30 ] || die "Backend not healthy after ${i} attempts. Check: ./deploy.sh multica logs"
  printf '.'
  sleep 2
done
printf '\n'
ok "Backend healthy"

# ── API token ─────────────────────────────────────────────────────────────────
if [ -z "${MULTICA_API_TOKEN:-}" ]; then
  echo
  echo "  Open http://127.0.0.1:${FRONTEND_PORT} in a browser."
  echo "  Create an account, then:"
  echo "    Settings → Personal → API Tokens → New Token"
  echo
  if [ -t 0 ]; then
    read -r -p "  Paste your API token: " MULTICA_API_TOKEN
  fi
  [ -n "${MULTICA_API_TOKEN:-}" ] || die "API token required. Set MULTICA_API_TOKEN env var or run interactively."
fi

AUTH="Authorization: Bearer $MULTICA_API_TOKEN"

# ── Discover workspace ────────────────────────────────────────────────────────
say "Fetching workspaces"
ws_json="$(http_get "$BASE/api/workspaces" "$AUTH")" \
  || die "Failed to list workspaces. Check your token."
[ -n "$ws_json" ] || die "Empty response — check your token and that the backend is reachable."

WS_ID="$(python3 - "$ws_json" <<'PY'
import json, sys
raw = sys.argv[1]
d = json.loads(raw)
ws = d.get("workspaces", d) if isinstance(d, dict) else d
if not ws:
    raise SystemExit("No workspaces found — create one at the web UI first.")
print(ws[0]["id"])
PY
)"
WS_NAME="$(python3 - "$ws_json" <<'PY'
import json, sys
raw = sys.argv[1]
d = json.loads(raw)
ws = d.get("workspaces", d) if isinstance(d, dict) else d
print(ws[0].get("name", "?"))
PY
)"
ok "Workspace: $WS_NAME (id: $WS_ID)"

# ── Create agents + squad (single Python pass) ────────────────────────────────
say "Creating agents"
_summary="$(python3 - "$TEAM_JSON" "$BASE" "$WS_ID" "$MULTICA_API_TOKEN" <<'PY'
import json, sys, urllib.request, urllib.error

team_file, base, ws_id, token = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
auth = f"Bearer {token}"
hdrs = {"Authorization": auth, "Content-Type": "application/json"}

def api_get(path):
    req = urllib.request.Request(f"{base}{path}", headers={"Authorization": auth})
    return json.loads(urllib.request.urlopen(req).read())

def api_post(path, body):
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(), headers=hdrs, method="POST")
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}

# Build provider → runtime_id map; normalize 'claude-code' → 'claude'
runtimes = api_get(f"/api/runtimes?workspace_id={ws_id}")
rt_map = {}
for r in runtimes:
    p = r["provider"]
    rt_map[p] = r["id"]
rt_map.setdefault("claude-code", rt_map.get("claude", ""))

with open(team_file) as f:
    team = json.load(f)

created, skipped, leader_id = 0, 0, None

leader_name = next((a["name"] for a in team["agents"] if a.get("squad_leader")), None)

for a in team["agents"]:
    prov = a["provider"]
    rt = rt_map.get("claude" if prov == "claude-code" else prov, "")
    if not rt:
        print(f"WN No runtime for '{prov}' — skipping {a['name']}")
        skipped += 1
        continue
    body = {"name": a["name"], "role": a["role"],
            "description": a["description"], "runtime_id": rt}
    if a.get("instructions"):
        body["instructions"] = a["instructions"]
    code, resp = api_post(f"/api/agents?workspace_id={ws_id}", body)
    if code in (200, 201):
        if a.get("squad_leader"):
            leader_id = resp.get("id")
        print(f"OK Created: {a['name']} ({a['role']}, provider: {prov})")
        created += 1
    elif code == 409:
        print(f"SK Already exists: {a['name']} (skipped)")
        skipped += 1
    else:
        print(f"WN Could not create {a['name']} (HTTP {code})")
        skipped += 1

# Fetch all existing agents (need ids for squad membership)
existing_agents = api_get(f"/api/agents?workspace_id={ws_id}")
agent_id_map = {ag["name"]: ag["id"] for ag in existing_agents}

# If leader_id is still unset (agents were skipped), look up from existing list
if not leader_id and leader_name:
    leader_id = agent_id_map.get(leader_name)

# ── Squad: find existing or create ───────────────────────────────────────────
squad = team["squad"]
squad_name = squad["name"]
existing_squads = api_get(f"/api/squads?workspace_id={ws_id}")
squad_id = next((s["id"] for s in existing_squads if s["name"] == squad_name), None)

if squad_id:
    print(f"SK Squad '{squad_name}' already exists")
else:
    sq_body = {"name": squad_name, "description": squad["description"]}
    if leader_id:
        sq_body["leader_id"] = leader_id
    if squad.get("instructions"):
        sq_body["instructions"] = squad["instructions"]
    code, resp = api_post(f"/api/squads?workspace_id={ws_id}", sq_body)
    if code in (200, 201):
        squad_id = resp.get("id")
        print(f"OK Squad '{squad_name}' created")
    else:
        print(f"WN Could not create squad (HTTP {code}) — add it manually in the web UI")

# ── Squad members: add all agents ────────────────────────────────────────────
if squad_id:
    members_resp = api_get(f"/api/squads/{squad_id}/members?workspace_id={ws_id}")
    existing_member_ids = {m["member_id"] for m in members_resp}

    m_created = m_skipped = 0
    for a in team["agents"]:
        agent_id = agent_id_map.get(a["name"])
        if not agent_id:
            continue
        if agent_id in existing_member_ids:
            m_skipped += 1
            continue
        role = a.get("squad_role", "leader" if a.get("squad_leader") else "member")
        code, _ = api_post(
            f"/api/squads/{squad_id}/members?workspace_id={ws_id}",
            {"member_id": agent_id, "member_type": "agent", "role": role})
        if code in (200, 201):
            m_created += 1
        else:
            print(f"WN Could not add {a['name']} to squad (HTTP {code})")

    total = m_created + m_skipped
    if m_created:
        print(f"OK Added {m_created} member(s) to '{squad_name}' ({m_skipped} already in squad)")
    elif m_skipped == total and total > 0:
        print(f"SK All {total} agents already in squad '{squad_name}'")

# ── Skills: create workspace skills + attach to agents ───────────────────────
# PUT /api/agents/{id}/skills is replace-all, so we build the full desired skill
# set per agent and only call PUT when it differs from what's already attached.
existing_skills = api_get(f"/api/skills?workspace_id={ws_id}")
skill_id_map = {s["name"]: s["id"] for s in existing_skills}

for sk in team.get("skills", []):
    sk_name = sk["name"]
    if sk_name not in skill_id_map:
        code, resp = api_post(f"/api/skills?workspace_id={ws_id}",
                              {"name": sk_name, "description": sk.get("description", ""),
                               "content": sk.get("content", "")})
        if code in (200, 201):
            skill_id_map[sk_name] = resp.get("id")
            print(f"OK Created skill: {sk_name}")
        else:
            print(f"WN Could not create skill '{sk_name}' (HTTP {code})")
    else:
        print(f"SK Skill already exists: {sk_name}")

# Build desired skill_ids per agent from team.json
agent_desired_skills = {}
for sk in team.get("skills", []):
    skill_id = skill_id_map.get(sk["name"])
    if not skill_id:
        continue
    for agent_name in sk.get("agents", []):
        agent_desired_skills.setdefault(agent_name, []).append(skill_id)

for agent_name, desired_ids in agent_desired_skills.items():
    agent_id = agent_id_map.get(agent_name)
    if not agent_id:
        continue
    current = api_get(f"/api/agents/{agent_id}/skills?workspace_id={ws_id}")
    current_ids = {s["id"] for s in (current if isinstance(current, list) else [])}
    if current_ids == set(desired_ids):
        print(f"SK Skills already set: {agent_name}")
        continue
    # Merge: keep any extra skills the agent already has, add desired on top
    merged = list(current_ids | set(desired_ids))
    req = urllib.request.Request(
        f"{base}/api/agents/{agent_id}/skills?workspace_id={ws_id}",
        data=json.dumps({"skill_ids": merged}).encode(), headers=hdrs, method="PUT")
    try:
        urllib.request.urlopen(req)
        print(f"OK Attached skills → {agent_name}")
    except Exception as e:
        print(f"WN Could not set skills for {agent_name}: {e}")

print(f"SUMMARY:{created}:{skipped}")
PY
)"

created=0; skipped=0
while IFS= read -r line; do
  case "$line" in
    OK*) ok  "${line#OK }" ; created=$((created+1)) ;;
    SK*) ok  "${line#SK }" ; skipped=$((skipped+1)) ;;
    WN*) warn "${line#WN }" ; skipped=$((skipped+1)) ;;
    SUMMARY:*) created="${line#SUMMARY:}"; created="${created%%:*}"
               skipped="${line##*:}" ;;
  esac
done <<< "$_summary"

# ── Autopilots hint ───────────────────────────────────────────────────────────
echo
say "Autopilots (configure in web UI: Settings → Autopilots):"
python3 - "$TEAM_JSON" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    t = json.load(f)
for ap in t.get("autopilots", []):
    trigger = ap.get("schedule", ap.get("trigger", "manual"))
    print(f"  • {ap['name']:25s} agent={ap['agent']:8s} trigger={trigger}")
PY

echo
say "Done. $created created, $skipped skipped."
echo
echo "  Next steps:"
echo "  1. Settings → Autopilots → create the 3 autopilots listed above"
echo "  2. Create your first issue and assign it to an agent"
