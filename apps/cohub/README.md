# Cohub

**A private personal-coworker control plane and deterministic workflow engine for Hermes Agent.**

Cohub turns autonomous agent work into durable, inspectable operations. It owns workflow routing, retries, approvals, checkpoints, artifacts, and append-only traces while Hermes owns reasoning, tools, skills, memory, messaging, and model access.

## Why Cohub

Autonomous agents are excellent at open-ended work but should not own every control-flow decision. Cohub separates responsibilities:

- **Code owns:** legal routes, state transitions, leases, retries, budgets, approvals, persistence, and completion criteria.
- **The agent owns:** step content, tool use, structured outputs, and evidence-backed choices among declared routes.
- **The operator owns:** protected-action approval and policy.

## Current MVP

- Immutable versioned workflow definitions with SHA-256 fingerprints.
- Node types: `task`, `decision`, `parallel`, `human`, and `end`.
- JSON-schema subset validation for step outputs.
- Durable SQLite task, run, step, approval, artifact, and event state.
- Atomic state transitions and append-only per-run event sequences.
- Worker leases that prevent duplicate claims and recover after expiry.
- Retry exhaustion and deterministic decision-route enforcement.
- Parallel fan-out with direct-branch convergence.
- Human approval and side-effect gates bound to exact payload hashes.
- Artifact path containment and SHA-256 metadata.
- Process restart and checkpoint/resume behavior.
- Hermes Runs API executor plus deterministic local executor.
- Durable Hermes tool-approval bridge with same-run resume and cancellation propagation.
- Hermes Agent plugin tools.
- Responsive local dashboard with Today, Tasks, Runs, Workflows, Approvals, and Artifacts views.
- Token-protected JSON API.

## Quick start

Cohub has no runtime dependency outside Python 3.11+.

```bash
git clone https://github.com/duthaho/cohub.git
cd cohub
python3 -m cohub --data-dir .cohub demo --approve
python3 -m cohub --data-dir .cohub serve --port 8765
```

Open <http://127.0.0.1:8765>.

To protect the API even on localhost:

```bash
export COHUB_API_TOKEN='replace-with-a-random-secret'
python3 -m cohub --data-dir .cohub serve
```

Use the **API token** button in the dashboard to save the token in browser local storage.

## Hermes executor

The local executor is deterministic and intended for tests and demos. Configure Hermes for real agent steps:

```bash
export HERMES_API_BASE='http://127.0.0.1:8642'
export HERMES_API_KEY='your-hermes-api-key'
python3 -m cohub --data-dir ~/.hermes/cohub serve --executor hermes
```

Each claimed step is submitted with a stable Hermes `session_id` for correlation:

```text
<cohub-run-id>:<step-id>:<attempt>
```

The correlation ID does not replace provider idempotency or read-back reconciliation for external writes.

### Hermes tool approvals

Hermes and Cohub enforce separate approval boundaries. Cohub gates declared
workflow intent; Hermes gates the concrete tool action selected inside the
agent turn. Keep both enabled.

When a Hermes run enters `waiting_for_approval`, Cohub reads the redacted
`approval.request` event, persists the Hermes run ID and exact review payload,
releases the worker lease, and exposes one payload-hashed Cohub approval. The
dashboard intentionally maps approval to Hermes `once` and rejection to
`deny`; it never offers `session` or `always` grants. Resolving the approval
requeues the same Cohub step and reconciles the same Hermes run instead of
submitting a duplicate. Cohub cancellation and executor timeout propagate to
Hermes `/stop`.

Hermes must advertise `run_approval_response` and `approval_events` through
`/v1/capabilities`. If the SSE approval detail cannot be recovered, Cohub
fails closed rather than inventing a review payload.

The Hermes response must be a JSON object:

```json
{
  "status": "completed",
  "output": {"report": "..."},
  "route": "approved",
  "reason": "All required checks passed"
}
```

## Install as a Hermes plugin

Cohub supports the native directory plugin and pip entry-point layouts.

```bash
hermes plugins install duthaho/cohub --no-enable
hermes plugins list
hermes plugins enable cohub
```

The plugin registers:

- `cohub_publish_workflow`
- `cohub_start_run`
- `cohub_run_status`
- `cohub_tick_run`
- `cohub_resolve_approval`

Newly installed Hermes plugins are intentionally opt-in. Restart Hermes after enabling the plugin.

## Workflow example

```json
{
  "name": "verified-report",
  "start": "draft",
  "nodes": {
    "draft": {
      "type": "task",
      "prompt": "Draft the report and return structured output.",
      "output_schema": {"type": "object", "required": ["report"]},
      "next": "approve"
    },
    "approve": {
      "type": "human",
      "payload": {"action": "deliver", "target": "telegram"},
      "next": "done"
    },
    "done": {"type": "end"}
  }
}
```

See [`examples/personal-daily-briefing.json`](examples/personal-daily-briefing.json) for parallel collection, a planned transient failure, retry, artifact creation, payload-bound approval, and completion.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/overview` | Dashboard aggregate |
| `GET` | `/api/tasks` | List tasks |
| `GET` | `/api/runs` | List runs |
| `GET` | `/api/runs/{id}` | Full run, external execution, approval, and event trace |
| `POST` | `/api/workflows` | Validate and publish an immutable workflow |
| `POST` | `/api/runs` | Start a workflow run |
| `POST` | `/api/runs/{id}/tick` | Execute at most one ready step |
| `POST` | `/api/runs/{id}/pause` | Pause a run |
| `POST` | `/api/runs/{id}/resume` | Resume a paused run |
| `POST` | `/api/runs/{id}/cancel` | Cancel a run |
| `POST` | `/api/approvals/{id}/approve` | Approve an exact payload hash |
| `POST` | `/api/approvals/{id}/reject` | Reject a protected action |

## Verification

```bash
npm ci
npm test
npm run build
npx playwright install chromium
npm run test:e2e
python3 -W error::ResourceWarning -m unittest discover -s tests -v
python3 -m py_compile cohub/*.py
python3 -m cohub --data-dir /tmp/cohub-demo demo --approve
```

## Dashboard development

The operator dashboard is a React and TypeScript application built with Vite.
Source files live in `frontend/`; the reproducible production bundle is written
to `cohub/static/` and is served by Cohub's standard-library HTTP server. The
production container uses a Node build stage, then copies only the static bundle
into the final Python image, so Node is not present at runtime.

```bash
npm install
npm run dev     # Vite development server
npm run build   # Type-check and create the production bundle
npm run test:e2e # Desktop and mobile Chromium smoke tests
```

The dashboard remains local-first: it loads no remote fonts, scripts, analytics,
or other third-party browser resources. API authorization and all workflow and
approval invariants remain server-side.

## Project documents

- [Architecture](docs/ARCHITECTURE.md)
- [Security model](SECURITY.md)
- [Implementation plan](.hermes/plans/2026-08-24_040411-cohub-mvp.md)
- [Contributing](CONTRIBUTING.md)

## Scope boundary

The MVP deliberately does not rebuild browser automation, memory, messaging gateways, model providers, or skills. Hermes already owns those capabilities. Cohub is the durable workflow and operator-control layer.

## License

MIT
