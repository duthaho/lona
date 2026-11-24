"""Isolated HTTP contract tests: fixtures are test doubles, never live Multica."""
import copy
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'test-only-secret-do-not-log'


class MulticaFixture:
    def __init__(self):
        self.workspaces = [{'id': 'ws-1', 'name': 'Test workspace'}]
        self.runtimes = [
            {'id': 'rt-claude', 'provider': 'claude', 'status': 'online'},
            {'id': 'rt-codex', 'provider': 'codex', 'status': 'online'},
        ]
        self.resources = {'agents': {}, 'squads': {}, 'skills': {}, 'labels': {}}
        self.statuses = []
        self.workflows = {}
        self.projects = {}
        self.users = [{'id': 'member-owner', 'email': 'owner@example.test'}]
        self.members = {}
        self.disabled = set()
        self.attachments = {}
        self.requests = []
        self.fail_readback_after_put = False
        self.fail_once = set()
        self.fail = set()
        self.redirect: str | None = None
        self.drop_fields = set()
        self.counter = 0
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def handle_request(self):
                parsed = urlparse(self.path)
                route = parsed.path
                body = None
                if self.command != 'GET':
                    body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                fixture.requests.append((self.command, route, body, parse_qs(parsed.query)))
                if fixture.redirect and route != '/api/config':
                    self.send_response(302)
                    self.send_header('Location', fixture.redirect)
                    self.end_headers()
                    return
                status, payload = fixture.dispatch(self.command, route, body)
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            do_GET = do_POST = do_PUT = handle_request

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def dispatch(self, method, route, body):
        if (method, route) in self.fail_once:
            self.fail_once.remove((method, route))
            return 500, {'error': 'one-shot test fault'}
        if (method, route) in self.fail or (method, '*') in self.fail:
            return 500, {'error': 'test fault injection', 'sensitive': TOKEN}
        if route == '/api/config':
            return 200, {}
        if route == '/api/workspaces':
            return 200, self.workspaces
        if route == '/api/runtimes':
            return 200, self.runtimes
        if route == '/api/users':
            return 200, self.users
        if route == '/api/me':
            return 200, {'id': 'member-owner'}
        if route == '/api/issue-statuses':
            if method == 'GET':
                return 200, copy.deepcopy(self.statuses)
            if method == 'POST':
                status = dict(body, id=f'status-{len(self.statuses) + 1}')
                self.statuses.append(status)
                return 201, copy.deepcopy(status)
        if route == '/api/issue-workflows':
            if method == 'GET':
                return 200, list(copy.deepcopy(self.workflows).values())
            if method == 'POST':
                workflow = dict(body, id=f'workflow-{len(self.workflows) + 1}')
                self.workflows[workflow['id']] = workflow
                return 201, copy.deepcopy(workflow)
        if route.startswith('/api/issue-workflows/') and method == 'PUT':
            resource_id = route.rsplit('/', 1)[-1]
            self.workflows[resource_id].update(body)
            return 200, copy.deepcopy(self.workflows[resource_id])
        if route == '/api/projects':
            if method == 'GET':
                return 200, list(copy.deepcopy(self.projects).values())
            if method == 'POST':
                project = dict(body, id=f'project-{len(self.projects) + 1}')
                self.projects[project['id']] = project
                return 201, copy.deepcopy(project)
        if route.startswith('/api/projects/') and method == 'PUT':
            resource_id = route.rsplit('/', 1)[-1]
            self.projects[resource_id].update(body)
            return 200, copy.deepcopy(self.projects[resource_id])
        parts = route.strip('/').split('/')
        if len(parts) < 2 or parts[1] not in self.resources:
            return 404, {}
        kind = parts[1]
        resources = self.resources[kind]
        if len(parts) == 2:
            if method == 'GET':
                return 200, list(copy.deepcopy(resources).values())
            if method == 'POST':
                if kind in ('agents', 'skills', 'labels') and any(x['name'] == body['name'] for x in resources.values()):
                    return 409, {}
                self.counter += 1
                resource_id = f'{kind}-{self.counter}'
                resource = dict(body, id=resource_id, workspace_id='ws-1')
                # Real CreateSquad ignores instructions; only PUT persists them.
                if kind == 'squads':
                    resource.pop('instructions', None)
                for field in self.drop_fields:
                    resource.pop(field, None)
                resources[resource_id] = resource
                if kind == 'squads':
                    self.members[resource_id] = [{'member_type': 'agent', 'member_id': body['leader_id'], 'role': 'leader'}]
                return 201, copy.deepcopy(resource)
        resource_id = parts[2]
        if resource_id not in resources:
            return 404, {}
        if len(parts) == 3:
            if method == 'GET':
                return 200, copy.deepcopy(resources[resource_id])
            if method == 'PUT':
                resources[resource_id].update({k: v for k, v in body.items() if k not in self.drop_fields})
                if self.fail_readback_after_put:
                    self.fail_readback_after_put = False
                    self.fail_once.add(('GET', route))
                return 200, copy.deepcopy(resources[resource_id])
        if kind == 'squads' and parts[3] == 'members':
            members = self.members[resource_id]
            if method == 'GET':
                return 200, copy.deepcopy(members)
            if method == 'POST':
                members.append(body)
                return 201, body
            if method == 'PUT':
                for member in members:
                    if member['member_id'] == body['member_id'] and member['member_type'] == body['member_type']:
                        member.update(body)
                        return 200, member
        if kind == 'agents' and parts[3] == 'skills':
            if method == 'GET':
                return 200, [dict(self.resources['skills'].get(i, {'id': i}), enabled=(i not in self.disabled)) for i in self.attachments.get(resource_id, [])]
            if method in ('PUT', 'POST'):
                previous = self.attachments.get(resource_id, []) if parts[-1] == 'add' else []
                self.attachments[resource_id] = sorted(set(previous) | set(body['skill_ids']))
                return 200, []
        return 404, {}


class TeamContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MulticaFixture()
        self.addCleanup(self.fixture.close)
        self.tmp = tempfile.TemporaryDirectory(prefix='lona-team-test-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'config/multica').mkdir(parents=True)
        (self.home / 'scripts').mkdir()
        for name in ('seed-team.sh', 'team.json'):
            shutil.copy2(ROOT / 'config/multica' / name, self.home / 'config/multica' / name)
        module = ROOT / 'scripts/multica_team.py'
        if module.exists():
            shutil.copy2(module, self.home / 'scripts/multica_team.py')
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        # Legacy script needs wget; use real curl HTTP transport on minimal hosts.
        shim = self.bin / 'wget'
        shim.write_text('#!/usr/bin/env python3\nimport subprocess,sys\na=["curl","--fail","--silent"]\nfor v in sys.argv[1:]:\n if v=="-qO-": continue\n if v.startswith("--header="): a.extend(["--header",v.split("=",1)[1]])\n else: a.append(v)\nraise SystemExit(subprocess.run(a).returncode)\n')
        shim.chmod(0o755)

    def run_seed(self, *args, **env):
        settings = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        MULTICA_API_TOKEN=TOKEN, MULTICA_BACKEND_PORT=str(self.fixture.server.server_port),
                        MULTICA_WORKSPACE_ID='')
        settings.update(env)
        return subprocess.run(['bash', 'config/multica/seed-team.sh', *args], cwd=self.home,
                              env=settings, capture_output=True, text=True, timeout=15)

    def writes(self):
        return [r for r in self.fixture.requests if r[0] != 'GET']

    def module(self):
        spec = importlib.util.spec_from_file_location('multica_team_test_module', self.home / 'scripts/multica_team.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fresh_setup_persists_squad_policy_then_rerun_has_no_writes(self):
        first = self.run_seed()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        manifest = json.loads((self.home / 'config/multica/team.json').read_text())
        squad = next(iter(self.fixture.resources['squads'].values()))
        self.assertEqual(squad.get('instructions'), manifest['squad']['instructions'])
        self.assertEqual(len(self.fixture.resources['agents']), len(manifest['agents']))
        self.fixture.requests.clear()
        second = self.run_seed()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.writes(), [], 'Repeated setup must be idempotent')

    def test_missing_labels_are_created_without_overwriting_existing_labels(self):
        self.fixture.resources['labels']['existing'] = {
            'id': 'label-existing', 'name': 'bug', 'color': '#000000',
            'description': 'Workspace customization', 'resource_type': 'issue',
        }
        result = self.run_seed()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads((self.home / 'config/multica/team.json').read_text())
        expected = {label['name'] for label in manifest['labels']}
        actual = {label['name'] for label in self.fixture.resources['labels'].values()}
        self.assertEqual(actual, expected)
        existing = self.fixture.resources['labels']['existing']
        self.assertEqual(existing['color'], '#000000')
        self.assertEqual(existing['description'], 'Workspace customization')
        writes = [request for request in self.writes() if request[1] == '/api/labels']
        self.assertEqual(len(writes), len(expected) - 1)
        self.fixture.requests.clear()
        second = self.run_seed()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.writes(), [])

    def test_duplicate_manifest_agent_is_rejected_before_any_write(self):
        path = self.home / 'config/multica/team.json'
        team = json.loads(path.read_text())
        team['agents'].append(copy.deepcopy(team['agents'][0]))
        path.write_text(json.dumps(team))
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.writes(), [])

    def test_sync_updates_managed_policy_but_preserves_runtime_and_custom_edits(self):
        first = self.run_seed()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        agents = {a['name']: a for a in self.fixture.resources['agents'].values()}
        agents['Atlas']['runtime_id'] = 'operator-selected-runtime'
        agents['Atlas']['model'] = 'operator-selected-model'
        path = self.home / 'config/multica/team.json'
        team = json.loads(path.read_text())
        atlas = next(a for a in team['agents'] if a['name'] == 'Atlas')
        atlas['instructions'] += '\nUpdated template policy.'
        path.write_text(json.dumps(team))
        original = agents['Atlas']['instructions']
        seed = self.run_seed()
        self.assertEqual(seed.returncode, 0, seed.stderr)
        self.assertEqual(agents['Atlas']['instructions'], original)
        sync = self.run_seed('--sync')
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        self.assertEqual(agents['Atlas']['instructions'], atlas['instructions'])
        self.assertEqual(agents['Atlas']['runtime_id'], 'operator-selected-runtime')
        self.assertEqual(agents['Atlas']['model'], 'operator-selected-model')
        agents['Atlas']['instructions'] = 'Human custom instructions'
        drift = self.run_seed('--sync')
        self.assertNotEqual(drift.returncode, 0, drift.stdout)
        self.assertEqual(agents['Atlas']['instructions'], 'Human custom instructions')

    def test_ambiguous_runtime_requires_explicit_selection(self):
        self.fixture.runtimes.append({'id': 'rt-other', 'provider': 'claude', 'status': 'online'})
        ambiguous = self.run_seed()
        self.assertNotEqual(ambiguous.returncode, 0, ambiguous.stdout)
        self.assertEqual(self.writes(), [])
        explicit = self.run_seed('--runtime', 'claude=rt-other')
        self.assertEqual(explicit.returncode, 0, explicit.stdout + explicit.stderr)
        lore = next(a for a in self.fixture.resources['agents'].values() if a['name'] == 'Lore')
        self.assertEqual(lore['runtime_id'], 'rt-other')

    def test_workspace_ambiguity_is_read_only_until_explicit_selection(self):
        self.fixture.workspaces.append({'id': 'ws-2', 'name': 'Other workspace'})
        ambiguous = self.run_seed()
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertEqual(self.writes(), [])
        selected = self.run_seed('--workspace-id', 'ws-1')
        self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
        for method, path, body, query in self.fixture.requests:
            if method != 'GET':
                self.assertEqual(query['workspace_id'], ['ws-1'])

    def test_schema_v2_dry_run_is_read_only(self):
        dry = self.run_seed('--dry-run')
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertEqual(self.writes(), [])
        self.assertIn('Would ensure workflow Lona Delivery', dry.stdout)

    def test_dry_run_reads_all_schema_v2_dependencies(self):
        result = self.run_seed('--dry-run')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reads = {path for method, path, _, _ in self.fixture.requests if method == 'GET'}
        self.assertTrue({'/api/agents', '/api/squads', '/api/skills', '/api/runtimes',
                         '/api/issue-statuses', '/api/issue-workflows', '/api/projects',
                         '/api/users', '/api/me'} <= reads)
        self.assertEqual(self.writes(), [])

    def test_v2_validation_rejects_unknown_status(self):
        team = json.loads((self.home / 'config/multica/team.json').read_text())
        team['workflow']['steps'][0]['status_key'] = 'not-a-status'
        module = self.module()
        with self.assertRaisesRegex(module.SetupError, 'unknown status'):
            module.validate_team(team)

    def test_v2_validation_rejects_unknown_handler(self):
        team = json.loads((self.home / 'config/multica/team.json').read_text())
        team['workflow']['steps'][1]['handler'] = {'type': 'agent', 'name': 'Unknown'}
        module = self.module()
        with self.assertRaisesRegex(module.SetupError, 'unknown agent'):
            module.validate_team(team)

    def test_v2_validation_rejects_project_workflow_mismatch(self):
        team = json.loads((self.home / 'config/multica/team.json').read_text())
        team['project']['workflow'] = 'Other workflow'
        module = self.module()
        with self.assertRaisesRegex(module.SetupError, 'Project workflow'):
            module.validate_team(team)

    def test_reviewer_email_rejects_zero_matching_members(self):
        self.fixture.users = []
        result = self.run_seed('--sync', MULTICA_REVIEWER_EMAIL='missing@example.test')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not resolve', result.stderr)

    def test_reviewer_email_rejects_ambiguous_members(self):
        self.fixture.users.append({'id': 'member-duplicate', 'email': 'owner@example.test'})
        result = self.run_seed('--sync', MULTICA_REVIEWER_EMAIL='owner@example.test')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not resolve', result.stderr)

    def test_schema_v2_second_run_has_no_status_workflow_or_project_writes(self):
        self.assertEqual(self.run_seed().returncode, 0)
        self.fixture.requests.clear()
        self.assertEqual(self.run_seed().returncode, 0)
        paths = [path for method, path, _, _ in self.writes()]
        self.assertFalse(any(path.startswith('/api/issue-statuses') or path.startswith('/api/issue-workflows')
                             or path.startswith('/api/projects') for path in paths))

    def test_sync_updates_workflow_and_project(self):
        self.assertEqual(self.run_seed().returncode, 0)
        path = self.home / 'config/multica/team.json'
        team = json.loads(path.read_text())
        team['workflow']['description'] += ' revised'
        team['workflow']['steps'][1]['instructions'] += ' revised'
        team['project']['description'] += ' revised'
        path.write_text(json.dumps(team))
        result = self.run_seed('--sync')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        workflow = next(iter(self.fixture.workflows.values()))
        project = next(iter(self.fixture.projects.values()))
        self.assertEqual(workflow['description'], team['workflow']['description'])
        self.assertEqual(project['description'], team['project']['description'])

    def test_safe_export_rejects_email(self):
        module = self.module()
        with self.assertRaisesRegex(module.SetupError, 'Refusing export'):
            module.safe_export({'instructions': 'contact reviewer@example.test'})

    def test_safe_export_rejects_identity_field(self):
        module = self.module()
        with self.assertRaisesRegex(module.SetupError, 'Refusing export'):
            module.safe_export({'workspace_id': 'ws-1'})

    def test_missing_online_runtime_is_rejected_before_any_write(self):
        self.fixture.runtimes[1]['status'] = 'offline'
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.writes(), [])

    def test_squad_policy_failure_can_resume_without_duplicate_resources(self):
        self.fixture.fail.add(('PUT', '*'))
        first = self.run_seed()
        self.assertNotEqual(first.returncode, 0)
        self.fixture.fail.clear()
        second = self.run_seed()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(self.fixture.resources['squads']), 1)
        squad = next(iter(self.fixture.resources['squads'].values()))
        self.assertTrue(squad.get('instructions'))

    def manifest_agents(self):
        return json.loads((self.home / 'config/multica/team.json').read_text())['agents']

    def test_skill_creation_failure_can_resume(self):
        self.fixture.fail.add(('POST', '/api/skills'))
        first = self.run_seed()
        self.assertNotEqual(first.returncode, 0)
        self.fixture.fail.clear()
        second = self.run_seed()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(self.fixture.resources['agents']), len(self.manifest_agents()))
        self.assertEqual(len(self.fixture.resources['skills']), 2)

    def test_attachment_failure_exits_nonzero(self):
        self.assertEqual(self.run_seed().returncode, 0)
        agent_id = next(iter(self.fixture.resources['agents']))
        self.fixture.attachments[agent_id] = []
        self.fixture.fail.add(('POST', f'/api/agents/{agent_id}/skills/add'))
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_additive_skill_attach_preserves_custom_disabled_skills(self):
        self.assertEqual(self.run_seed().returncode, 0)
        agent_id = next(iter(self.fixture.resources['agents']))
        desired = set(self.fixture.attachments[agent_id])
        self.fixture.attachments[agent_id] = ['custom-extra']
        self.fixture.disabled.add('custom-extra')
        result = self.run_seed()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(set(self.fixture.attachments[agent_id]), desired | {'custom-extra'})
        self.assertIn('custom-extra', self.fixture.disabled)
        self.fixture.requests.clear()
        self.assertEqual(self.run_seed().returncode, 0)
        self.assertEqual(self.writes(), [])

    def test_successful_http_response_with_ignored_fields_is_not_success(self):
        self.fixture.drop_fields.add('instructions')
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('readback mismatch', result.stderr)

    def test_redirect_does_not_send_bearer_token_to_another_endpoint(self):
        self.fixture.redirect = f'http://127.0.0.1:{self.fixture.server.server_port}/stolen'
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(r[1] == '/stolen' for r in self.fixture.requests))
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_explicit_adoption_backs_up_policy_but_not_runtime_secrets(self):
        self.assertEqual(self.run_seed().returncode, 0)
        agent = next(iter(self.fixture.resources['agents'].values()))
        agent['instructions'] = 'Custom policy to back up'
        agent['custom_env'] = {'SECRET': TOKEN}
        result = self.run_seed('--sync', '--adopt-existing')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backups = list((self.home / 'data/multica').glob('team-policy-*.json'))
        self.assertTrue(any('Custom policy to back up' in p.read_text() for p in backups))
        for path in (self.home / 'data/multica').glob('*.json'):
            self.assertNotIn(TOKEN, path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_manifest_declares_live_agent_concurrency(self):
        manifest = json.loads((self.home / 'config/multica/team.json').read_text())
        self.assertTrue(all(isinstance(a.get('max_concurrent_tasks'), int) and a['max_concurrent_tasks'] >= 1
                            for a in manifest['agents']))
        result = self.run_seed()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for method, path, body, query in self.writes():
            if path == '/api/agents':
                self.assertGreaterEqual(body['max_concurrent_tasks'], 1)
                self.assertNotIn('role', body)  # Not an upstream agent API field.
                self.assertNotIn('coordination', body)  # Local prompt policy only.

    def test_disabled_required_skill_is_reported_not_silently_enabled(self):
        self.assertEqual(self.run_seed().returncode, 0)
        agent_id = next(iter(self.fixture.attachments))
        skill_id = self.fixture.attachments[agent_id][0]
        self.fixture.disabled.add(skill_id)
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(skill_id, self.fixture.disabled)
        self.assertIn('disabled', result.stderr)

    def test_squad_create_with_failed_readback_never_claims_empty_policy_verified(self):
        label_count = len(json.loads((self.home / 'config/multica/team.json').read_text())['labels'])
        self.fixture.fail_once.add(('GET', f'/api/squads/squads-{label_count + len(self.manifest_agents()) + 1}'))
        first = self.run_seed()
        self.assertNotEqual(first.returncode, 0)
        retry = self.run_seed()
        self.assertNotEqual(retry.returncode, 0, retry.stdout)
        self.assertNotIn('resources verified', retry.stdout)
        adopted = self.run_seed('--sync', '--adopt-existing')
        self.assertEqual(adopted.returncode, 0, adopted.stdout + adopted.stderr)
        squad = next(iter(self.fixture.resources['squads'].values()))
        self.assertTrue(squad['instructions'])
        self.assertEqual(len(self.fixture.resources['squads']), 1)

    def test_interrupted_sync_readback_recovers_ownership_for_future_updates(self):
        self.assertEqual(self.run_seed().returncode, 0)
        path = self.home / 'config/multica/team.json'
        team = json.loads(path.read_text())
        team['agents'][0]['instructions'] += '\nPolicy version two.'
        path.write_text(json.dumps(team))
        self.fixture.fail_readback_after_put = True
        failed = self.run_seed('--sync')
        self.assertNotEqual(failed.returncode, 0)
        recovered = self.run_seed('--sync')
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        team['agents'][0]['instructions'] += '\nPolicy version three.'
        path.write_text(json.dumps(team))
        future = self.run_seed('--sync')
        self.assertEqual(future.returncode, 0, future.stdout + future.stderr)
        agent = next(iter(self.fixture.resources['agents'].values()))
        self.assertEqual(agent['instructions'], team['agents'][0]['instructions'])
        agent['instructions'] = 'Human custom edit after recovery'
        self.assertNotEqual(self.run_seed('--sync').returncode, 0)
        self.assertEqual(agent['instructions'], 'Human custom edit after recovery')

    def test_interrupted_final_state_write_recovers_only_pre_authorized_policy(self):
        self.assertEqual(self.run_seed().returncode, 0)
        path = self.home / 'config/multica/team.json'
        team = json.loads(path.read_text())
        team['agents'][0]['instructions'] += '\nPolicy two after disk fault.'
        path.write_text(json.dumps(team))
        spec = importlib.util.spec_from_file_location('multica_team_fault_test', self.home / 'scripts/multica_team.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        client = module.Client(f'http://127.0.0.1:{self.fixture.server.server_port}', TOKEN)
        with module.Registry(self.home / 'data/multica/team-state.json') as registry:
            original_write = registry.write
            saw_intent = False

            def fail_final(target, content):
                nonlocal saw_intent
                if target == registry.path:
                    if content.get('pending'):
                        saw_intent = True
                    elif saw_intent:
                        raise OSError('intentional final state-write fault')
                return original_write(target, content)

            registry.write = fail_final
            with self.assertRaisesRegex(OSError, 'intentional final'):
                module.provision(client, team, registry=registry, sync=True)
        state = json.loads((self.home / 'data/multica/team-state.json').read_text())
        self.assertTrue(state['pending'])
        retry = self.run_seed('--sync')
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertEqual(json.loads((self.home / 'data/multica/team-state.json').read_text())['pending'], {})
        team['agents'][0]['instructions'] += '\nPolicy three after recovery.'
        path.write_text(json.dumps(team))
        self.assertEqual(self.run_seed('--sync').returncode, 0)

    def test_changed_existing_squad_leader_is_preserved_but_fails_acceptance(self):
        self.assertEqual(self.run_seed().returncode, 0)
        squad = next(iter(self.fixture.resources['squads'].values()))
        other_id = next(a['id'] for a in self.fixture.resources['agents'].values() if a['name'] == 'Atlas')
        squad['leader_id'] = other_id
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('leader', result.stderr)
        self.assertEqual(squad['leader_id'], other_id)
        self.assertNotEqual(self.run_seed('--sync', '--adopt-existing').returncode, 0)
        self.assertEqual(squad['leader_id'], other_id)

    def test_provisioning_failure_exits_nonzero_without_leaking_response_or_token(self):
        self.fixture.fail.add(('POST', '*'))
        result = self.run_seed()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertNotIn('Done.', result.stdout)


if __name__ == '__main__':
    unittest.main()
