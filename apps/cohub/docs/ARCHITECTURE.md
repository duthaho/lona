# Cohub Architecture

## Responsibilities

Cohub is a control plane, not another agent runtime.

```text
Dashboard / API / Hermes tools
             |
      WorkflowEngine
  routing | approvals | leases
             |
        CohubStore
 SQLite state | append-only events
             |
       WorkerService
             |
 LocalExecutor or HermesRunsExecutor
             |
     Hermes Agent + external tools
```

## Durable entities

- `workflow_versions`: immutable normalized workflow documents and fingerprints.
- `tasks`: operator goals.
- `runs`: one execution pinned to one workflow version.
- `steps`: attempts, leases, outputs, routes, and errors.
- `approvals`: exact payload plus SHA-256, decision, and note.
- `external_executions`: durable provider run ID, attempt, status, and last error.
- `artifacts`: relative path, content hash, and size.
- `events`: append-only monotonic sequence per run.

## State ownership

The engine is the only component allowed to advance workflow state. Executors return `StepResult`; they cannot schedule the next step directly.

```text
pending → ready → leased → completed
                   └──────→ ready (retry)
                   └──────→ failed

run: queued → running → waiting_for_human → running → completed
                       ├─→ paused → running
                       └─→ failed/cancelled
```

## Determinism guarantees

1. Every run stores the workflow version and fingerprint used at creation.
2. Decision output is accepted only when `route` is declared by that node.
3. Output is validated before a step becomes complete.
4. A step is activated at most once after completion.
5. Direct parallel branches must all complete before the parallel continuation is activated.
6. Run events receive a strictly increasing sequence inside the same state transaction.
7. A worker claim is guarded by a lease and compare-and-set update.
8. A protected action cannot become ready until the exact payload hash is approved.

## Parallel MVP boundary

The MVP supports direct parallel branches and one continuation on the `parallel` node. Branch subgraphs and explicit `join` policies are planned for the next engine version. The validator and documentation do not claim arbitrary DAG convergence yet.

## Restart behavior

SQLite is the source of truth. After restart:

- Completed steps stay completed.
- Pending approvals remain pending.
- Hermes run IDs survive Cohub restart, so waiting steps reconcile the same external run.
- Expired leases can be reclaimed.
- A pinned workflow version remains available even after a newer version is published.
- Artifacts remain on disk and retain content hashes in SQLite.

## Hermes integration

The plugin exposes deterministic operations as Hermes tools. The Runs API executor submits one step at a time with a stable correlation session and a strict JSON response contract. The returned Hermes run ID is persisted before Cohub waits for completion.

Hermes tool approvals are bridged as a durable external-run state machine:

```text
submit Hermes run → persist external_run_id → reconcile status
                                             ├─ completed → complete Cohub step
                                             ├─ failed → declared retry/failure policy
                                             └─ waiting_for_approval
                                                    ↓
                                      consume redacted SSE approval.request
                                                    ↓
                                      payload-hashed Cohub approval
                                                    ↓
                                      once/deny → same Hermes run → reconcile
```

Polling exposes that a run is waiting, but the reviewable approval details are
only available from Hermes' SSE event stream. Cohub therefore never fabricates
approval details after a missed event. Worker leases are released while the
operator decides. Duplicate local resolution is idempotent, and a Hermes 409
is accepted only after status confirms that the run is no longer waiting.

## Dashboard boundary

The dashboard is a React and TypeScript application compiled by Vite into static
assets served by the standard-library API. Node is a build-time dependency only;
the production image and Python package contain the generated assets, not a Node
server. The browser loads no third-party resources, preserving local-first and
Content Security Policy guarantees.

The interface is organized around operator decisions rather than storage tables:

- Overview prioritizes pending approvals and active runs.
- Runs support search and status filters and open in a contextual inspector.
- The run inspector owns step progress, Hermes execution state, and artifacts.
- Approval review presents intent and Hermes-redacted command data first; raw
  payload and SHA-256 details remain available behind progressive disclosure.
- Workflow selection and graph inspection share one workspace.

The frontend does not contain business invariants. API authentication, workflow
validation, approval hash checks, redaction, and state transitions are enforced
server-side.
