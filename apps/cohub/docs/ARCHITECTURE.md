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
- Expired leases can be reclaimed.
- A pinned workflow version remains available even after a newer version is published.
- Artifacts remain on disk and retain content hashes in SQLite.

## Hermes integration

The plugin exposes deterministic operations as Hermes tools. The Runs API executor submits one step at a time with an idempotency key and a strict JSON response contract. This preserves Cohub control-flow ownership while allowing Hermes to use its full model, skill, tool, browser, and messaging environment.

## Dashboard boundary

The dashboard is a static responsive application served by the standard-library API. It does not contain business invariants. API authentication, workflow validation, approval hash checks, and state transitions are enforced server-side.
