#!/usr/bin/env python3
"""Fail-closed, non-destructive Multica team provisioning (Python standard library)."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


class SetupError(Exception):
    """An actionable provisioning failure safe to print without response bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A redirect must never forward a bearer token to another host.


class Client:
    def __init__(self, base: str, token: str, timeout: float = 10):
        self.base = base.rstrip('/')
        self.token = token
        self.timeout = timeout
        self.workspace = ''
        self.opener = urllib.request.build_opener(NoRedirect())
        self.writes = 0

    def request(self, method, path, body=None):
        query = urllib.parse.urlencode({'workspace_id': self.workspace}) if self.workspace else ''
        url = self.base + path + ('?' + query if query else '')
        data = None if body is None else json.dumps(body).encode()
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise SetupError(f'{method} {path}: response exceeds 4 MiB')
                result = json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            error.close()
            raise SetupError(f'{method} {path}: HTTP {error.code}; setup incomplete') from None
        except (OSError, ValueError):
            raise SetupError(f'{method} {path}: connection or JSON error; setup incomplete') from None
        if method != 'GET':
            self.writes += 1
        return result

    def listing(self, path):
        result = self.request('GET', path)
        if isinstance(result, dict):
            collections = [value for value in result.values()
                           if isinstance(value, list) and all(isinstance(item, dict) and item.get('id') for item in value)]
            if len(collections) == 1:
                result = collections[0]
        if not isinstance(result, list) or any(not isinstance(x, dict) or not x.get('id') for x in result):
            raise SetupError(f'GET {path}: expected an array of resources with IDs')
        return result

    def detail(self, kind, resource_id):
        safe_id = urllib.parse.quote(resource_id, safe='')
        result = self.request('GET', f'/api/{kind}/{safe_id}')
        if not isinstance(result, dict) or result.get('id') != resource_id:
            raise SetupError(f'GET {kind}: invalid resource identity')
        return result

    def verify(self, kind, resource_id, expected):
        actual = self.detail(kind, resource_id)
        if any(actual.get(k) != value for k, value in expected.items()):
            raise SetupError(f'{kind} {resource_id}: readback mismatch; setup incomplete')
        return actual


def by_name(resources, kind):
    result = {}
    for item in resources:
        name = item.get('name')
        if not isinstance(name, str) or name in result:
            raise SetupError(f'{kind}: missing or ambiguous resource name')
        result[name] = item
    return result


def provider_name(provider):
    return 'claude' if provider == 'claude-code' else provider


def validate_team(team):
    """Validate all references before any HTTP mutation (metadata is local policy)."""
    if not isinstance(team, dict) or team.get('schema_version', 1) not in (1, 2):
        raise SetupError('Unsupported team manifest schema_version')
    agents = team.get('agents')
    if not isinstance(agents, list) or not agents:
        raise SetupError('Manifest requires non-empty agents')
    names = set()
    for agent in agents:
        if not isinstance(agent, dict) or any(not isinstance(agent.get(k), str) or not agent[k].strip()
                                               for k in ('name', 'provider', 'description', 'instructions')):
            raise SetupError('Every agent requires a name, provider, description and instructions')
        if agent['name'] in names:
            raise SetupError('Duplicate manifest agent name')
        names.add(agent['name'])
        concurrency = agent.get('max_concurrent_tasks', 1)
        if type(concurrency) is not int or not 1 <= concurrency <= 10:
            raise SetupError('max_concurrent_tasks must be an integer from 1 to 10')
    if sum(agent.get('squad_leader') is True for agent in agents) != 1:
        raise SetupError('Manifest requires exactly one squad leader')
    squad = team.get('squad')
    if not isinstance(squad, dict) or any(not isinstance(squad.get(k), str) or not squad[k].strip()
                                         for k in ('name', 'description', 'instructions')):
        raise SetupError('Squad requires name, description and instructions')
    skills = team.get('skills', [])
    if not isinstance(skills, list):
        raise SetupError('Manifest skills must be an array')
    skill_names = set()
    for skill in skills:
        if not isinstance(skill, dict) or any(not isinstance(skill.get(k), str) or not skill[k].strip()
                                             for k in ('name', 'description', 'content')):
            raise SetupError('Every skill requires name, description and content')
        if skill['name'] in skill_names:
            raise SetupError('Duplicate manifest skill name')
        skill_names.add(skill['name'])
        assigned = skill.get('agents', [])
        if not isinstance(assigned, list) or any(not isinstance(a, str) or a not in names for a in assigned):
            raise SetupError('Skill references an unknown agent')
    labels = team.get('labels', [])
    if not isinstance(labels, list):
        raise SetupError('Manifest labels must be an array')
    label_names = set()
    for label in labels:
        if not isinstance(label, dict) or any(not isinstance(label.get(k), str) or not label[k].strip()
                                              for k in ('name', 'color', 'description')):
            raise SetupError('Every label requires a name, color and description')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', label['color']):
            raise SetupError('Every label color must be a six-digit hex value')
        if label['name'] in label_names:
            raise SetupError('Duplicate manifest label name')
        label_names.add(label['name'])
    if team.get('schema_version', 1) == 1:
        return team
    statuses = team.get('statuses', [])
    if not isinstance(statuses, list):
        raise SetupError('Manifest statuses must be an array')
    status_keys = {'backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled'}
    for status in statuses:
        if not isinstance(status, dict) or any(not isinstance(status.get(k), str) or not status[k].strip()
                                               for k in ('key', 'name', 'category', 'color')):
            raise SetupError('Every custom status requires key, name, category and color')
        if status['key'] in status_keys or status['key'].lower() != status['key']:
            raise SetupError('Custom status key is invalid or duplicates a system status')
        status_keys.add(status['key'])
    workflow = team.get('workflow')
    if not isinstance(workflow, dict) or any(not isinstance(workflow.get(k), str) or not workflow[k].strip()
                                             for k in ('name', 'description')):
        raise SetupError('Schema v2 requires a named workflow')
    steps = workflow.get('steps')
    if not isinstance(steps, list) or not steps:
        raise SetupError('Workflow requires non-empty steps')
    for step in steps:
        if not isinstance(step, dict) or step.get('status_key') not in status_keys:
            raise SetupError('Workflow references an unknown status')
        handler = step.get('handler', {'type': 'none'})
        if not isinstance(handler, dict) or handler.get('type') not in ('none', 'agent', 'squad', 'member'):
            raise SetupError('Workflow handler must be none, agent, squad or member')
        name = handler.get('name', '')
        if handler['type'] == 'agent' and name not in names:
            raise SetupError('Workflow handler references an unknown agent')
        if handler['type'] == 'squad' and name != squad['name']:
            raise SetupError('Workflow handler references an unknown squad')
        if handler['type'] == 'member' and name not in ('workspace_owner', 'reviewer'):
            raise SetupError('Member handler must be workspace_owner or reviewer')
    project = team.get('project')
    if not isinstance(project, dict) or any(not isinstance(project.get(k), str) or not project[k].strip()
                                            for k in ('title', 'description', 'workflow', 'repo')):
        raise SetupError('Schema v2 requires a project with title, description, workflow and repo')
    if project['workflow'] != workflow['name']:
        raise SetupError('Project workflow must name the manifest workflow')
    return team


def safe_export(team):
    """Reject accidental identity or credential-shaped values before writing an export."""
    encoded = json.dumps(team, sort_keys=True).lower()
    forbidden = ('workspace_id', 'runtime_id', 'owner_id', 'created_by', 'bearer ')
    if any(value in encoded for value in forbidden) or re.search(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b', encoded):
        raise SetupError('Refusing export containing an identity, email or secret-shaped field')
    return team


def resolve_handler(client, handler, agents, squads):
    kind = handler.get('type', 'none')
    if kind == 'none':
        return {'type': 'none'}
    if kind == 'agent':
        return {'type': 'agent', 'id': agents[handler['name']]['id']}
    if kind == 'squad':
        return {'type': 'squad', 'id': squads[handler['name']]['id']}
    reviewer = os.environ.get('MULTICA_REVIEWER_EMAIL', '').strip()
    if reviewer:
        people = client.listing('/api/users')
        matches = [person for person in people if person.get('email') == reviewer]
        if len(matches) != 1:
            raise SetupError('MULTICA_REVIEWER_EMAIL did not resolve to exactly one workspace member')
        return {'type': 'member', 'id': matches[0]['id']}
    me = client.request('GET', '/api/me')
    if not isinstance(me, dict) or not isinstance(me.get('id'), str):
        raise SetupError('Cannot resolve workspace owner; set MULTICA_REVIEWER_EMAIL')
    return {'type': 'member', 'id': me['id']}


def workflow_payload(team, client, agents, squads):
    steps = []
    for spec in team['workflow']['steps']:
        step = {'status_key': spec['status_key'], 'handler': resolve_handler(client, spec.get('handler', {}), agents, squads),
                'instructions': spec.get('instructions', '')}
        for key in ('next_status_key', 'back_status_key'):
            if spec.get(key):
                step[key] = spec[key]
        steps.append(step)
    return steps


def comparable_steps(steps):
    keys = ('status_key', 'handler', 'instructions', 'next_status_key', 'back_status_key')
    return [{key: step.get(key, {'type': 'none'} if key == 'handler' else '') for key in keys} for step in steps]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Registry:
    """Local ownership ledger; hashes only. Lock prevents concurrent seed writers."""
    def __init__(self, path):
        self.path = path
        self.data = {'version': 1, 'resources': {}, 'pending': {}}

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or self.path.with_suffix('.lock').is_symlink():
            raise SetupError('Refusing symlinked provisioning state')
        fd = os.open(self.path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
        self.lock = os.fdopen(fd, 'a+')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise SetupError('Another team setup is running; retry after it finishes') from None
        try:
            if self.path.exists():
                self.data = json.loads(self.path.read_text())
                if not isinstance(self.data, dict) or self.data.get('version') != 1 or not isinstance(self.data.get('resources'), dict):
                    raise SetupError('Invalid provisioning state; preserve it and investigate before retrying')
            if not isinstance(self.data.setdefault('pending', {}), dict):
                raise SetupError('Invalid pending-operation state')
        except BaseException:
            self.lock.close()
            raise
        return self

    def __exit__(self, *args):
        self.lock.close()

    def write(self, target, content):
        fd, temp = tempfile.mkstemp(prefix='.team-', dir=target.parent)
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(content, handle, indent=2, sort_keys=True)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def key(self, client, kind, resource_id):
        return fingerprint([client.base, client.workspace, kind, resource_id])

    def remember(self, key, fields):
        self.data['resources'][key] = {k: fingerprint(v) for k, v in fields.items()}
        self.data['pending'].pop(key, None)
        self.write(self.path, self.data)

    def reconcile(self, client, kind, actual, fields, sync, adopt):
        resource_id = actual['id']
        key = self.key(client, kind, resource_id)
        current = {k: actual.get(k, '') for k in fields}
        pending = self.data['pending'].get(key)
        if pending:
            hashes = {k: fingerprint(v) for k, v in current.items()}
            if not isinstance(pending, dict) or not isinstance(pending.get('before'), dict) or not isinstance(pending.get('after'), dict):
                raise SetupError('Invalid pending policy operation; inspect state before retrying')
            if hashes == pending['after'] or hashes == pending['before'] and current == fields:
                # Only a durable pre-authorized intent may recover a new baseline.
                # Matching an arbitrary unmanaged resource never establishes ownership.
                self.remember(key, current)
            elif hashes != pending['before'] and not adopt:
                raise SetupError(f'{kind} {resource_id}: pending policy diverged; human edits preserved')
        if current == fields:
            if adopt:
                self.remember(key, fields)
            return
        if not sync:
            print('Preserved existing', kind, actual['name'], '(template differs; explicit --sync required)', flush=True)
            return
        previous = self.data['resources'].get(key)
        if not adopt and (not previous or any(previous.get(k) != fingerprint(v) for k, v in current.items())):
            raise SetupError(f'{kind} {resource_id}: unmanaged/customized policy preserved; inspect before --sync --adopt-existing')
        # Only policy fields are backed up; never env, model, runtime or credentials.
        backup = self.path.parent / f'team-policy-{time.time_ns()}.json'
        self.write(backup, {'kind': kind, 'id': resource_id, 'workspace_id': client.workspace, 'previous': current})
        self.data['pending'][key] = {'before': {k: fingerprint(v) for k, v in current.items()},
                                     'after': {k: fingerprint(v) for k, v in fields.items()}}
        self.write(self.path, self.data)  # Intent is durable BEFORE the HTTP mutation.
        client.request('PUT', f'/api/{kind}/' + urllib.parse.quote(resource_id, safe=''), fields)
        client.verify(kind, resource_id, fields)
        self.remember(key, fields)
        print('Updated managed policy:', kind, actual['name'], flush=True)


def provision(client, team, workspace_id='', *, registry, sync=False, adopt=False, runtime_overrides=None, dry_run=False):
    workspaces = client.listing('/api/workspaces')
    selected = [w for w in workspaces if w['id'] == workspace_id] if workspace_id else workspaces
    if len(selected) != 1:
        raise SetupError('Select one workspace explicitly with --workspace-id (or MULTICA_WORKSPACE_ID)')
    client.workspace = selected[0]['id']
    print('Workspace:', selected[0].get('name', client.workspace), flush=True)
    labels = by_name([label for label in client.listing('/api/labels')
                      if label.get('resource_type') == 'issue'], 'issue labels')
    agents = by_name(client.listing('/api/agents'), 'agents')
    squads = by_name(client.listing('/api/squads'), 'squads')
    skills = by_name(client.listing('/api/skills'), 'skills')
    runtimes = client.listing('/api/runtimes')
    runtime_ids = {}
    for spec in team['agents']:
        if spec['name'] in agents:
            continue
        provider = provider_name(spec['provider'])
        options = [r for r in runtimes if provider_name(r.get('provider')) == provider and r.get('status') == 'online']
        selected_runtime = (runtime_overrides or {}).get(provider)
        if selected_runtime:
            options = [r for r in options if r['id'] == selected_runtime]
        if len(options) != 1:
            raise SetupError(f'Need exactly one online runtime for {provider}; authenticate/start the daemon or select --runtime {provider}=ID')
        runtime_ids[spec['name']] = options[0]['id']

    # Runtime selection is fully validated before the first mutation. Statuses
    # are then first in the schema-v2 dependency order.
    if team['schema_version'] == 2:
        statuses = by_name(client.listing('/api/issue-statuses'), 'issue statuses')
        known_status_keys = {item.get('key') for item in statuses.values()}
        for spec in team.get('statuses', []):
            if spec['key'] in known_status_keys:
                continue
            if dry_run:
                continue
            created = client.request('POST', '/api/issue-statuses', spec)
            if not isinstance(created, dict) or created.get('key') != spec['key']:
                raise SetupError('POST issue-statuses: readback mismatch')
            known_status_keys.add(spec['key'])

    if dry_run:
        # Validate every read dependency and handler before declaring a plan.
        if team['schema_version'] == 2:
            planned_agents = dict(agents)
            planned_agents.update({spec['name']: {'id': 'planned-' + spec['name']} for spec in team['agents'] if spec['name'] not in agents})
            planned_squads = dict(squads)
            if team['squad']['name'] not in planned_squads:
                planned_squads[team['squad']['name']] = {'id': 'planned-squad'}
            workflow_payload(team, client, planned_agents, planned_squads)
            client.listing('/api/issue-workflows')
            client.listing('/api/projects')
            for spec in team['statuses']:
                print('Would ensure issue status', spec['key'], flush=True)
            print('Would ensure workflow', team['workflow']['name'], flush=True)
            print('Would ensure project', team['project']['title'], flush=True)
        for spec in team['agents']:
            print('Would ensure agent', spec['name'], flush=True)
        for spec in team.get('labels', []):
            if spec['name'] not in labels:
                print('Would create label', spec['name'], flush=True)
        print('Would ensure squad', team['squad']['name'], flush=True)
        print('Dry run complete; 0 API writes.', flush=True)
        return

    for spec in team.get('labels', []):
        if spec['name'] in labels:
            continue
        created = client.request('POST', '/api/labels', {**spec, 'resource_type': 'issue'})
        if not isinstance(created, dict) or not isinstance(created.get('id'), str) or created.get('name') != spec['name']:
            raise SetupError('POST labels: readback mismatch')
        labels[spec['name']] = created
        print('Created label', spec['name'], flush=True)

    def ensure(kind, spec, existing, fields):
        policy_keys = ('description', 'content') if kind == 'skills' else ('description', 'instructions')
        policy = {k: fields[k] for k in policy_keys if k in fields}
        if spec['name'] in existing:
            actual = client.detail(kind, existing[spec['name']]['id'])
            if kind != 'squads':  # Squad policy is reconciled together after create.
                registry.reconcile(client, kind, actual, policy, sync, adopt)
            return actual
        result = client.request('POST', f'/api/{kind}', fields)
        if not isinstance(result, dict) or not isinstance(result.get('id'), str):
            raise SetupError(f'POST {kind}: missing resource ID; setup incomplete')
        result = client.verify(kind, result['id'], fields)
        existing[spec['name']] = result
        tracked = {k: result.get(k, '') for k in policy_keys}
        registry.remember(registry.key(client, kind, result['id']), tracked)
        print('Created', kind, spec['name'], flush=True)
        return result

    for spec in team['agents']:
        fields = {k: spec[k] for k in ('name', 'description', 'instructions')}
        fields['runtime_id'] = runtime_ids.get(spec['name'], '')
        fields['max_concurrent_tasks'] = spec.get('max_concurrent_tasks', 1)
        ensure('agents', spec, agents, fields)

    squad_spec = team['squad']
    leader = next(a for a in team['agents'] if a.get('squad_leader'))
    leader_id = agents[leader['name']]['id']
    squad_new = squad_spec['name'] not in squads
    fields = {k: squad_spec[k] for k in ('name', 'description')}
    fields['leader_id'] = leader_id
    squad = ensure('squads', squad_spec, squads, fields)
    if squad.get('leader_id') != leader_id:
        raise SetupError('Existing squad leader differs from manifest; preserved without changes. Inspect the squad and explicitly resolve leadership in the UI before retrying.')
    squad_path = '/api/squads/' + urllib.parse.quote(squad['id'], safe='')
    # Upstream CreateSquad has no instructions field: a separate PUT is required.
    key = registry.key(client, 'squads', squad['id'])
    snapshot = registry.data['resources'].get(key, {})
    resume = not squad.get('instructions') and snapshot.get('instructions') == fingerprint('')
    policy = {k: squad_spec[k] for k in ('description', 'instructions')}
    registry.reconcile(client, 'squads', squad, policy, sync or squad_new or resume, adopt)
    if not client.detail('squads', squad['id']).get('instructions'):
        raise SetupError('Squad coordinator policy is empty and ownership is unverified; inspect before --sync --adopt-existing')
    members = client.request('GET', squad_path + '/members')
    if not isinstance(members, list):
        raise SetupError('Squad members: expected an array')
    for spec in team['agents']:
        member_id = agents[spec['name']]['id']
        if any(m.get('member_type') == 'agent' and m.get('member_id') == member_id for m in members):
            continue
        member = {'member_type': 'agent', 'member_id': member_id, 'role': spec.get('squad_role', 'member')}
        client.request('POST', squad_path + '/members', member)
        members = client.request('GET', squad_path + '/members')
        if not isinstance(members, list) or member not in [{k: m.get(k) for k in member} for m in members]:
            raise SetupError('Squad membership readback mismatch')
    for spec in team.get('skills', []):
        ensure('skills', spec, skills, {k: spec.get(k, '') for k in ('name', 'description', 'content')})
    for spec in team['agents']:
        desired = {skills[s['name']]['id'] for s in team.get('skills', []) if spec['name'] in s.get('agents', [])}
        path = '/api/agents/' + urllib.parse.quote(agents[spec['name']]['id'], safe='') + '/skills'
        current = client.listing(path)
        current_ids = {s['id'] for s in current}
        if any(s['id'] in desired and s.get('enabled') is False for s in current):
            raise SetupError('A required team skill is disabled; operator must inspect/enable it explicitly')
        if desired <= current_ids:
            continue
        # Additive endpoint preserves custom assignments AND disabled-skill settings.
        client.request('POST', path + '/add', {'skill_ids': sorted(desired - current_ids)})
        attached = client.listing(path)
        if not desired <= {s['id'] for s in attached} or any(s['id'] in desired and s.get('enabled') is False for s in attached):
            raise SetupError('Skill attachment readback mismatch or required skill disabled')
    if team['schema_version'] == 2:
        workflows = by_name(client.listing('/api/issue-workflows'), 'workflows')
        desired_steps = workflow_payload(team, client, agents, squads)
        workflow_spec = team['workflow']
        existing_workflow = workflows.get(workflow_spec['name'])
        if not existing_workflow:
            if dry_run:
                print('Would create workflow', workflow_spec['name'], flush=True)
            else:
                created = client.request('POST', '/api/issue-workflows', {'name': workflow_spec['name'], 'description': workflow_spec['description'], 'steps': desired_steps})
                if not isinstance(created, dict) or not created.get('id'):
                    raise SetupError('POST issue-workflows: missing resource ID')
                existing_workflow = created
        elif comparable_steps(existing_workflow.get('steps', [])) != comparable_steps(desired_steps) or existing_workflow.get('description') != workflow_spec['description']:
            if not sync:
                print('Preserved existing workflow', workflow_spec['name'], '(explicit --sync required)', flush=True)
            elif dry_run:
                print('Would update workflow', workflow_spec['name'], flush=True)
            else:
                client.request('PUT', '/api/issue-workflows/' + urllib.parse.quote(existing_workflow['id'], safe=''),
                               {'name': workflow_spec['name'], 'description': workflow_spec['description'], 'steps': desired_steps})
        project_list = client.listing('/api/projects')
        projects = {item.get('title'): item for item in project_list}
        if None in projects or len(projects) != len(project_list):
            raise SetupError('projects: missing or ambiguous project title')
        project_spec = team['project']
        project = projects.get(project_spec['title'])
        workflow_id = (existing_workflow or next((item for item in client.listing('/api/issue-workflows') if item.get('name') == workflow_spec['name']), {})).get('id')
        if not workflow_id:
            raise SetupError('Workflow was not available for project binding')
        fields = {'title': project_spec['title'], 'description': project_spec['description'], 'workflow_id': workflow_id,
                  'resources': [{'resource_type': 'github_repo', 'resource_ref': {'url': project_spec['repo'], 'ref': project_spec.get('ref', 'main')}}]}
        if not project:
            if dry_run:
                print('Would create project', project_spec['title'], flush=True)
            else:
                created = client.request('POST', '/api/projects', fields)
                if not isinstance(created, dict) or not created.get('id'):
                    raise SetupError('POST projects: missing resource ID')
        elif project.get('workflow_id') != workflow_id or project.get('description') != project_spec['description']:
            if not sync:
                print('Preserved existing project', project_spec['title'], '(explicit --sync required)', flush=True)
            elif dry_run:
                print('Would update project', project_spec['title'], flush=True)
            else:
                client.request('PUT', '/api/projects/' + urllib.parse.quote(project['id'], safe=''), fields)
    print(f'Team resources verified; {client.writes} API writes. Reported policy differences remain preserved. Autopilots are NOT provisioned.')


def read_dotenv(path):
    """Read data, never execute .env as a shell script."""
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('\"\'')
    return result


def cli_json(workspace_id, *args):
    """Use the installed CLI for exports: its read contract is version-matched."""
    binary = os.environ.get('MULTICA_CLI_PATH', 'multica')
    command = [binary, '--workspace-id', workspace_id, *args, '--output', 'json']
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise SetupError('Installed multica CLI could not read export data')
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise SetupError('Installed multica CLI returned invalid export JSON') from None


def export_team(workspace_id, output):
    """Capture exactly one project's portable policy via verified CLI read paths."""
    if not workspace_id:
        raise SetupError('export requires --workspace-id to avoid an accidental workspace selection')
    projects = cli_json(workspace_id, 'project', 'list')
    if not isinstance(projects, list) or len(projects) != 1:
        raise SetupError('export requires exactly one project; select a workspace with one project')
    project = projects[0]
    workflow = cli_json(workspace_id, 'workflow', 'get', project['workflow_id'])
    resources = cli_json(workspace_id, 'project', 'resource', 'list', project['id'])
    repositories = [r for r in resources if r.get('resource_type') == 'github_repo']
    if len(repositories) != 1:
        raise SetupError('export requires exactly one GitHub repository resource')
    reference = repositories[0].get('resource_ref', {})
    squads = cli_json(workspace_id, 'squad', 'list')
    if not isinstance(squads, list) or len(squads) != 1:
        raise SetupError('export requires exactly one squad')
    squad = cli_json(workspace_id, 'squad', 'get', squads[0]['id'])
    members = cli_json(workspace_id, 'squad', 'member', 'list', squad['id'])
    agents = cli_json(workspace_id, 'agent', 'list')
    skills = cli_json(workspace_id, 'skill', 'list')
    runtimes = {item['id']: provider_name(item.get('provider', ''))
                for item in cli_json(workspace_id, 'runtime', 'list')}
    statuses = cli_json(workspace_id, 'issue-status', 'list')
    names = {item['id']: item['name'] for item in agents}
    squad_names = {squad['id']: squad['name']}
    roles = {member.get('member_id'): member.get('role', 'member') for member in members}
    exported_agents = []
    for item in agents:
        exported_agents.append({key: item.get(key, '') for key in ('name', 'description', 'instructions', 'max_concurrent_tasks')})
        provider = runtimes.get(item.get('runtime_id'))
        if not provider:
            raise SetupError(f"Agent {item.get('name', '<unknown>')} has no exportable runtime provider")
        exported_agents[-1]['provider'] = provider
        exported_agents[-1]['squad_leader'] = item.get('id') == squad.get('leader_id')
        exported_agents[-1]['squad_role'] = roles.get(item.get('id'), 'member')
    steps = []
    for step in workflow.get('steps', []):
        handler = step.get('handler', {'type': 'none'})
        portable = {'type': handler.get('type', 'none')}
        if portable['type'] == 'agent':
            portable['name'] = names.get(handler.get('id'), '')
        elif portable['type'] == 'squad':
            portable['name'] = squad_names.get(handler.get('id'), '')
        elif portable['type'] == 'member':
            portable['name'] = 'workspace_owner'
        item = {'status_key': step.get('status_key'), 'handler': portable, 'instructions': step.get('instructions', '')}
        for key in ('next_status_key', 'back_status_key'):
            if step.get(key): item[key] = step[key]
        steps.append(item)
    custom = [item for item in statuses if not item.get('is_system')]
    result = {'schema_version': 2, 'squad': {key: squad.get(key, '') for key in ('name', 'description', 'instructions')},
              'agents': exported_agents,
              'skills': [{**{key: cli_json(workspace_id, 'skill', 'get', s['id'], '--with-content').get(key, '') for key in ('name', 'description', 'content')},
                          'agents': [agent['name'] for agent in agents if any(skill.get('name') == s.get('name') for skill in agent.get('skills', []))]} for s in skills],
              'statuses': [{key: s.get(key, '') for key in ('key', 'name', 'description', 'category', 'color', 'icon')} for s in custom],
              'workflow': {'name': workflow['name'], 'description': workflow.get('description', ''), 'steps': steps},
              'project': {'title': project['title'], 'description': project.get('description', ''), 'workflow': workflow['name'], 'repo': reference.get('url', ''), 'ref': reference.get('ref', '')}}
    safe_export(result)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print('Exported portable team manifest to', output, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('setup', 'export'), nargs='?', default='setup')
    parser.add_argument('--team', type=Path, default=Path('config/multica/team.json'))
    parser.add_argument('--workspace-id', default=os.environ.get('MULTICA_WORKSPACE_ID', ''))
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--runtime', action='append', default=[], metavar='PROVIDER=ID', help='Select an online runtime for new agents; repeat per provider')
    parser.add_argument('--sync', action='store_true', help='Update unchanged, previously managed policy fields')
    parser.add_argument('--adopt-existing', action='store_true', help='With --sync, explicitly replace unmanaged/customized policy (backed up first)')
    parser.add_argument('--dry-run', action='store_true', help='Validate and print the provisioning plan without API writes')
    parser.add_argument('--output', type=Path, help='Destination for export (required with export)')
    args = parser.parse_args()
    try:
        if args.adopt_existing and not args.sync:
            raise SetupError('--adopt-existing requires --sync')
        if not 0 < args.timeout <= 60:
            raise SetupError('--timeout must be greater than zero and at most 60 seconds')
        dotenv = read_dotenv(Path('.env'))
        port = os.environ.get('MULTICA_BACKEND_PORT') or dotenv.get('MULTICA_BACKEND_PORT') or '8083'
        if not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise SetupError('MULTICA_BACKEND_PORT must be a valid port')
        if args.command == 'export':
            if not args.output:
                raise SetupError('export requires --output')
            export_team(args.workspace_id, args.output)
            return 0
        token = os.environ.get('MULTICA_API_TOKEN', '') or os.environ.get('MULTICA_TOKEN', '')
        if not token and sys.stdin.isatty():
            import getpass
            token = getpass.getpass('Multica API token (Settings → Personal → API Tokens): ')
        if not token:
            raise SetupError('MULTICA_API_TOKEN (or MULTICA_TOKEN inside an agent run) is required')
        client = Client('http://127.0.0.1:' + port, token, args.timeout)
        client.request('GET', '/api/config')
        team = validate_team(json.loads(args.team.read_text()))
        runtime_overrides = {}
        for selection in args.runtime:
            provider, separator, resource_id = selection.partition('=')
            provider = provider_name(provider)
            if not separator or not resource_id or provider in runtime_overrides:
                raise SetupError('Runtime selections must be unique PROVIDER=ID entries')
            runtime_overrides[provider] = resource_id
        with Registry(Path('data/multica/team-state.json')) as registry:
            provision(client, team, args.workspace_id, registry=registry, sync=args.sync,
                      adopt=args.adopt_existing, runtime_overrides=runtime_overrides, dry_run=args.dry_run)
        return 0
    except (SetupError, OSError, ValueError, KeyError, StopIteration) as error:
        # Do not print exception text from JSON/transport code or server bodies.
        message = str(error) if isinstance(error, SetupError) else 'Invalid local configuration or API response'
        print('Team setup failed:', message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
