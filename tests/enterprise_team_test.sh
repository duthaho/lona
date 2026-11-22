#!/usr/bin/env bash
# Portable enterprise-team profiles, skills and non-destructive upgrades.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap sandbox_teardown EXIT
cd "$SANDBOX"

# The gateway and transactional updater must track the floating upstream tag.
grep -Fq 'image: ${HERMES_IMAGE:-nousresearch/hermes-agent:latest}' "$REPO_ROOT/docker-compose.hermes.yml" \
  || fail 'Hermes Compose default must use latest'
grep -Fq 'def="nousresearch/hermes-agent:latest"' scripts/selfupdate.sh \
  || fail 'Hermes updater default must match Compose latest'
grep -Fq 'dc pull "$PLATFORM"' deploy.sh || fail 'up must pull the image before starting'

# Fresh seed is part of the ordinary Hermes deployment, not a manual copy step.
fake_docker_setup
./deploy.sh hermes up > "$(dirname "$SANDBOX")/team-up.log" 2>&1
[ -f data/hermes/profiles/product/SOUL.md ] || fail 'product profile not seeded on up'
[ -f data/hermes/profiles/reviewer/profile.yaml ] || fail 'reviewer routing metadata missing'
[ -f data/hermes/profiles/developer/skills/test-driven-development/SKILL.md ] || fail 'developer TDD skill missing'
[ -f data/hermes/profiles/researcher/skills/grounded-citations/SKILL.md ] || fail 'researcher citations skill missing'
[ -f data/hermes/profiles/reviewer/skills/sdlc-review/SKILL.md ] || fail 'reviewer skill missing'
[ -f data/hermes/profiles/architect/skills/enterprise-delivery-team/SKILL.md ] || fail 'team skill missing'
[ -f data/hermes/skills/enterprise-delivery-team/SKILL.md ] || fail 'orchestrator team skill missing'
[ ! -e data/hermes/profiles/product/auth.json ] || fail 'profile copied credentials'
[ ! -e data/hermes/profiles/product/.env ] || fail 'profile copied secrets'
[ -f data/hermes/profiles/product/.lona-team-managed.json ] || fail 'seed ownership manifest missing'

python3 - <<'PY'
import json
from pathlib import Path
root=Path('data/hermes/profiles')
roles=json.loads(Path('config/hermes/team/roles.json').read_text())['profiles']
assert len(roles)==10
for role, spec in roles.items():
    folder=root/role
    for f in ('config.yaml','SOUL.md','profile.yaml'):
        assert (folder/f).is_file(), (role,f)
    for name in spec['skills']:
        assert (folder/'skills'/name/'SKILL.md').is_file(), (role,name)
print('   all 10 specialists and curated core skills seeded')
PY

# A malicious skill symlink must not pull host secrets into a portable profile.
ln -s "$SANDBOX/.env" config/hermes/team/skills/enterprise-delivery-team/secret-link
if python3 scripts/hermes_team.py seed --templates config/hermes --home "$SANDBOX/unsafe" >/dev/null 2>&1; then
  fail 'symlinked template skill was accepted'
fi
[ ! -e "$SANDBOX/unsafe/profiles/product" ] || fail 'partial profile created before bundle validation'
rm config/hermes/team/skills/enterprise-delivery-team/secret-link

# Reject credentials in vendored templates before any commit or deployment.
printf 'GITHUB_TOKEN=ghp_%030d\n' 0 >> config/hermes/team/skills/enterprise-delivery-team/SKILL.md
if python3 scripts/audit_team_bundle.py --templates config/hermes >/dev/null 2>&1; then
  fail 'credential-shaped value in bundled skill was accepted'
fi
cp "$REPO_ROOT/config/hermes/team/skills/enterprise-delivery-team/SKILL.md" config/hermes/team/skills/enterprise-delivery-team/SKILL.md
python3 scripts/audit_team_bundle.py --templates config/hermes

# Re-running up must preserve live profile identity and model choices.
printf '\n# user customization\n' >> data/hermes/profiles/product/SOUL.md
cp data/hermes/profiles/product/SOUL.md "$(dirname "$SANDBOX")/custom-soul"
docker_shim="$(command -v docker)"
mv "$docker_shim" "$docker_shim.base"
cat > "$docker_shim" <<'SH'
#!/usr/bin/env bash
if [ "${1:-}" = inspect ] && [ "${2:-}" = -f ]; then echo true; exit 0; fi
case " $* " in
  *' kanban boards list --json '*)
    if [ -f "$DOCKER_STATE/board_created" ]; then
      echo '[{"slug":"enterprise-apps"}]'
    else
      echo '[]'
    fi
    exit 0 ;;
  *' kanban boards create enterprise-apps '*)
    touch "$DOCKER_STATE/board_created"
    echo create >> "$DOCKER_STATE/board.log"
    exit 0 ;;
esac
exec "${0}.base" "$@"
SH
chmod +x "$docker_shim"
./deploy.sh hermes up > "$(dirname "$SANDBOX")/team-up-again.log" 2>&1
cmp -s data/hermes/profiles/product/SOUL.md "$(dirname "$SANDBOX")/custom-soul" || fail 'up overwrote custom SOUL'
[ -f "$DOCKER_STATE/board_created" ] || fail 'up did not create enterprise-apps board'
assert_eq 1 "$(wc -l < "$DOCKER_STATE/board.log")" 'board should be created once'

# Sync updates managed files but keeps user edits and live model/provider.
if python3 -c 'import yaml' >/dev/null 2>&1; then
  python3 - <<'PY'
from pathlib import Path
import yaml
p=Path('data/hermes/profiles/architect/config.yaml')
d=yaml.safe_load(p.read_text()); d['model']={'provider':'openai-codex','default':'custom-model'}
d['user_extension']={'keep':True}
p.write_text(yaml.safe_dump(d))
PY
  printf '\n# managed template update\n' >> config/hermes/profiles/architect/SOUL.md
  printf '\n# managed skill update\n' >> config/hermes/team/skills/enterprise-delivery-team/SKILL.md
  python3 scripts/hermes_team.py sync --templates config/hermes --home data/hermes
  python3 - <<'PY'
from pathlib import Path
import yaml
root=Path('data/hermes/profiles')
a=yaml.safe_load((root/'architect/config.yaml').read_text())
assert a['model']=={'provider':'openai-codex','default':'custom-model'}
assert a['user_extension']=={'keep':True}
assert a['agent']['reasoning_effort']=='high'
assert '# managed template update' in (root/'architect/SOUL.md').read_text()
assert '# user customization' in (root/'product/SOUL.md').read_text()
assert '# managed skill update' in (root/'architect/skills/enterprise-delivery-team/SKILL.md').read_text()
print('   sync preserves live model/custom identity and updates managed files')
PY
  python3 - <<'PY'
from pathlib import Path
import yaml
p=Path('data/hermes/config.yaml')
d=yaml.safe_load(p.read_text())
d['profile_routes']=[{'name':'my-existing-topic','platform':'telegram','chat_id':'-123','thread_id':'456','profile':'developer'}]
d['gateway']={'profile_routes':[{'name':'nested-route','platform':'telegram','profile':'researcher'}]}
p.write_text(yaml.safe_dump(d))
PY
  printf '\n# old local orchestrator\n' >> data/hermes/SOUL.md
  ./deploy.sh hermes sync > "$(dirname "$SANDBOX")/team-sync.log" 2>&1
  python3 - <<'PY'
from pathlib import Path
import yaml
root=Path('data/hermes')
d=yaml.safe_load((root/'config.yaml').read_text())
assert d['profile_routes'][0]['name']=='my-existing-topic'
assert d['gateway']['profile_routes'][0]['name']=='nested-route'
assert d['multiplex_profiles'] is True
assert '# old local orchestrator' not in (root/'SOUL.md').read_text()
assert list(Path('backups').glob('hermes-SOUL-*.md'))
a=yaml.safe_load((root/'profiles/architect/config.yaml').read_text())
assert a['model']['default']=='custom-model'
print('   deploy sync preserves live routes/model and backs up root SOUL')
PY
assert_eq 1 "$(wc -l < "$DOCKER_STATE/board.log")" 'sync should not recreate existing board'
else
  echo '   sync merge test skipped (PyYAML unavailable on host; required by deploy.sh hermes sync)'
fi

echo '   enterprise team portability ok'
