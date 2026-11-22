# Multica Build Team: contracts, operation and review policy

This is a deployment template, not a claim that a live team has been provisioned or
that prompt instructions enforce authorization. The supported API contract was
inspected at Multica revision `7720f0d597a29da5d2eb767225916a30e7b5af1f`.
Confirm compatibility with your deployed server before using this internal `/api`
interface. An unexpected response/endpoint fails closed; there is no speculative
fallback to a replace-all API. See [deployment operations](../config/multica/DEPLOYMENT.md).

## Why seven agents, not a larger organization

Keep Lore, Atlas, Forge, Canvas, Vessel, Sentinel and Prism. Assign clear tasks
directly to a capable specialist; use a squad only where routing/coordination adds
value. Anthropic's multi-agent guidance emphasizes explicit objectives, output,
resource limits and division of labor, and notes that tightly coupled coding is
less parallelizable than research [1]. OpenAI recommends maximizing a single
agent before adding multi-agent orchestration, layered guardrails and human
intervention for repeated failures/high-risk actions [2].

The manifest applies that guidance as conventions:

- One integration owner per verifiable vertical slice; isolated worktrees and
  disjoint file ownership before parallel writes; agreed API/schema contracts.
- At most three parallel slices and two author/reviewer correction cycles, then
  human triage. Every handoff specifies its own effort/time ceiling. These are
  **prompt rules, not mechanically enforced aggregate token/spend limits**.
- New agents set the native `max_concurrent_tasks` field to **1**. This is a real
  per-agent queue setting, not a global budget or a guarantee of isolated tools.
  Existing concurrency, runtime, model, credentials and permissions are preserved.
- Reviews must be capable, independent and bound to the exact commit SHA; risk
  triggers add architecture/security/QA evidence. CI **and** Security must pass;
  pending, absent or blocked checks never become a successful verdict.
- Humans merge and approve risk, production/shared-state mutations, credentials,
  destructive actions and new spend. Branch protection, scoped credentials and
  runtime isolation must enforce critical boundaries separately; this PR does
  not install those controls or change access.

## Two distinct modes for Lore

Multica injects a **coordinator-only** protocol into squad-leader turns [3]. Lore
must delegate implementation **and review**, record the required squad activity,
then stop. Leader-only custom squad instructions are not visible to members, so
all relevant constraints must be included in the issue/handoff.

A separate, direct assignment outside leader mode can authorize Lore to implement
or independently review. Lore-authored work always needs a qualified peer or
human; no self-approval. Forge/Canvas/Vessel review their respective technical
areas, Atlas reviews contracts, Sentinel security, Prism independently executes
acceptance. Cross-provider review is useful, not proof of independence. A quiet
leader records `no_action` through the injected protocol and avoids redundant
comments or mention-trigger loops.

## Provisioning without modifying live secrets

First create your account/workspace, authenticate the coding tools and connect a
Multica daemon **on the execution machine**. Installing a binary does not create
an authenticated online runtime or authorize repository access [4]. Create a
personal API token via the UI. Token input is via environment or an interactive
hidden prompt; it is never passed to child process arguments or stored in state.

```bash
# Existing exported token; MULTICA_BACKEND_PORT defaults to 8083.
./deploy.sh multica team-setup --workspace-id <workspace-uuid>

# Multiple online runtimes for a provider require explicit selection.
./deploy.sh multica team-setup --workspace-id <workspace-uuid> \
  --runtime claude=<runtime-uuid> --runtime codex=<runtime-uuid>

# Update only previously managed policy that has not been customized.
./deploy.sh multica team-setup --workspace-id <workspace-uuid> --sync

# Only after inspecting existing custom policy: explicit takeover of policy text.
./deploy.sh multica team-setup --workspace-id <workspace-uuid> \
  --sync --adopt-existing
```

The wrapper forwards arguments to `scripts/multica_team.py` (Python standard
library only; no wget, third-party Python library, or shell execution of `.env`).
Exported backend port takes precedence over the local dotenv value. The API
connection is fixed to loopback. HTTP calls are bounded by `--timeout` (default
10 seconds, maximum 60), response size is bounded, and redirects are rejected so
bearer tokens are not forwarded. API errors report method/path/status, not server
response bodies or credentials. The script never starts an agent task.

### Seed versus explicit policy reconciliation

Default setup creates missing agents, squad, membership, skills and additive skill
bindings. It checks workspace ambiguity and runtime availability for **new**
agents before any HTTP mutation. A rerun is idempotent with respect to existing
resources and extra assignments. Existing policy differences are reported and
preserved; successful resource setup does **not** mean that changed template text
has replaced existing text or that an agent has successfully executed a task.

`--sync` updates only description/instructions for tracked agents/squad and
managed skill description/content. State is scoped to backend/workspace/resource
identity, not just names. Current hashes must match the last managed values;
custom edits or unowned resources are preserved and return an incomplete-setup
error. `--sync --adopt-existing` explicitly permits replacing those policy fields,
including custom edits, with a private policy-only backup first. It does not
change runtime/model/env/access/concurrency. Review those backups as potentially
sensitive user-authored text; they are not database/application backups.

Local `data/multica/team-state.json` stores hashes (not tokens or whole agent
objects); `team-policy-*.json` backups store only prior managed policy fields.
Files are mode 0600, atomically replaced, and ignored by Git. Preserve them across
operator-machine moves if you want ownership tracking to continue. A local
advisory lock rejects overlapping setup runs using the same state path. This is
**not a distributed lock**: only one provisioning operator may target a workspace
at a time; do not run from separate machines/state directories concurrently.
Squad names are not unique upstream. Ambiguous names fail rather than silently
choosing a team; inspect/rename duplicates before retrying. No automatic deletion,
archival, membership removal or permission broadening is performed.

Failures are nonzero, including a successful HTTP response whose GET readback
omits requested fields. Writes are not a cross-resource transaction: an interrupted run can leave verified partial resources, and a later run safely
resumes or fails explicitly for operator inspection. Managed policy PUTs first
persist before/after hashes as a pending intent. After a readback or final state
write fails, a later run can recover ownership only by verifying that durable
intent; an arbitrary matching unmanaged resource is never silently claimed.
Custom edits that match neither state are preserved. In particular, a created squad with a failed initial policy PUT can resume from the
ownership ledger. On an indeterminate create/hidden-or-archived name conflict,
inspect identities before retrying/adopting; do not delete working resources to
force setup through.

### Actual API contracts, not invented fields

At the inspected revision [5–7]:

- Lists return arrays, not guessed cursor envelopes. Unexpected shapes fail.
- Agent `role` and the manifest's `coordination` object are local metadata, not
  API capability/permission fields. Descriptions/instructions and descriptive
  squad member roles carry the context.
- `POST /api/squads` ignores instructions; setup uses a separate
  `PUT /api/squads/{id}` and verifies GET readback. The leader is auto-added.
- Existing membership roles/leader choices are not automatically rewritten.
  A leader mismatch fails setup (including adoption), preserving the live choice;
  inspect the identity and explicitly resolve leadership in the UI before retrying.
  An empty unowned coordinator policy also fails rather than reporting a verified
  team; inspect before explicitly adopting it. Roles describe capabilities, not
  enforced permissions.
- Skill list responses omit content; comparison uses skill detail. Assignments
  use `POST /api/agents/{id}/skills/add`, never replace-all PUT. Custom and disabled
  extra skills are preserved; a disabled **required** team skill fails setup and
  needs explicit operator inspection/enablement rather than silently changing it.

## Wakeups and completion are not interchangeable

The delivery skill requires bounded event wakeups rather than sleep/poll loops.
Use the installed version's CLI help to set expiry and timeout behavior: the CLI
supplies no automatic deadline. Recheck every required PR and exact head on wake;
CI wakeups match **any** linked PR and checks finishing after rule creation, not an
all-PR acceptance barrier [8].

`in_review` means delivered for review, not `done`. Closed-child barriers require
`done`/`cancelled`; completing/cancelling also disables wakeups. Integrations may
auto-complete configured issues after merges. A `409 workflow_step_moved` means a
stale source step: leave status alone and request human inspection/new assignment,
not repeated retries to escape the workflow boundary [8].

## Autopilots remain suggestions

The three `autopilots` entries are **not provisioned**. They are bounded opt-in
suggestions, not fabricated success evidence. Upstream has separate autopilot and
trigger endpoints, with different payloads from this manifest [9]. A PR-opened
label does not register a GitHub webhook. Before any later automation rollout,
verify server version, target readiness, timezone, credentials/signing, identity,
deduplication, execution/cost limits and human approval.

## Verification boundary

`tests/test_multica_team.py` uses labeled local HTTP fixtures to exercise failure,
readback, retry, scope, ownership and API contract behavior. These are real code
executions against test doubles, **not live Multica deployment or provider runs**.
`tests/multica_team_test.sh` includes them in the normal suite. Actual server image
startup, coding-tool subscription authentication, repository permissions, agent
output quality and production recovery require separate authorized acceptance.

## Primary sources

All source-contract references below are pinned to
`7720f0d597a29da5d2eb767225916a30e7b5af1f`; compatibility with a different deployed
version must be checked, not assumed.

1. https://www.anthropic.com/engineering/multi-agent-research-system
2. https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents
3. https://github.com/multica-ai/multica/blob/7720f0d597a29da5d2eb767225916a30e7b5af1f/server/internal/handler/squad_briefing.go
4. https://github.com/multica-ai/multica/blob/7720f0d597a29da5d2eb767225916a30e7b5af1f/apps/docs/content/docs/daemon-runtimes.mdx
5. https://github.com/multica-ai/multica/blob/7720f0d597a29da5d2eb767225916a30e7b5af1f/server/internal/handler/agent.go
6. https://github.com/multica-ai/multica/blob/7720f0d597a29da5d2eb767225916a30e7b5af1f/server/internal/handler/squad.go
7. https://github.com/multica-ai/multica/blob/7720f0d597a29da5d2eb767225916a30e7b5af1f/server/internal/handler/skill.go
8. https://github.com/multica-ai/multica/blob/7720f0d597a29da5d2eb767225916a30e7b5af1f/apps/docs/content/docs/issue-wakeups.mdx
9. https://github.com/multica-ai/multica/blob/7720f0d597a29da5d2eb767225916a30e7b5af1f/server/internal/handler/autopilot.go
