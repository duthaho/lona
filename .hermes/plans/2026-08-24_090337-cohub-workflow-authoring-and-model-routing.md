# Cohub Workflow Authoring and Model Routing Plan

**Status:** Proposed
**Repository:** `duthaho/lona`
**Application:** `apps/cohub`
**Baseline:** `main` at `180e039`
**Plan date:** 2026-08-24

## Goal

Make Cohub usable by a non-technical operator: choose a reliable model for a run, build workflows visually from supported nodes, and ask Hermes to draft a workflow from natural language without exposing or requiring raw JSON.

## Current state and evidence

- Production `https://lona.duthaho.dev` returns HTTP 200 and serves the merged React bundle (`Cohub · Personal Coworker`, `/assets/index-COAe3xdu.js`). API health cannot be inspected without the private Cohub bearer token.
- Cohub currently submits Hermes runs with only `input`, `session_id`, and `instructions`; it does not send `model`, `provider`, or `model_options`.
- Hermes officially supports per-request `model`, `provider`, and `model_options` on `POST /v1/runs`.
- Hermes exposes authenticated, curated model metadata through `GET /api/model/options`; `/v1/models` is intentionally insufficient for a rich picker.
- Cohub supports `task`, `decision`, `parallel`, `human`, and `end` nodes, with deterministic validation and immutable published versions.
- The current UI publishes raw JSON and asks for JSON run input, both of which are unsuitable as the primary operator experience.

## Product decisions

1. Deliver model selection first because it directly addresses slow and unreliable runs.
2. Keep model discovery server-to-server. The browser calls Cohub; Cohub calls Hermes with the internal API key. Hermes credentials must never reach the browser.
3. Store a resolved model snapshot on every run and external execution. Historical runs must not change when aliases or provider defaults change.
4. Use this model precedence:
   - explicit run override;
   - node-level model;
   - workflow recommended model;
   - Hermes default and configured fallback chain.
5. Use `@xyflow/react` for the visual canvas. JSON remains an advanced read-only/export/import view, not the default editor.
6. Add durable workflow drafts. Publishing remains an explicit action that creates an immutable validated version.
7. Natural-language generation creates a draft only. It must never publish or execute automatically.
8. If Hermes cannot guarantee a tool-disabled generation request, generation must run through a dedicated Hermes profile with executable toolsets disabled.
9. The backend validator remains authoritative. Canvas restrictions are guidance, not security.

## Phase 0 — Live contract and capability probe

### Scope

- Add a bounded Hermes client operation for `/v1/capabilities` and `/api/model/options`.
- Verify the deployed Hermes version advertises run submission, status, SSE, stop, approval response, and model routing.
- Record the exact model-options response shape in a fixture without credentials or personal data.
- Define graceful states for Hermes unavailable, unsupported endpoint, no authenticated providers, and stale model catalog.

### Acceptance

- Cohub can return a redacted model catalog without exposing API keys, provider tokens, internal URLs, or raw upstream errors.
- Unsupported Hermes versions disable the picker and explain that the Hermes default will be used.
- Catalog calls use short timeouts and a small server-side cache so opening the modal cannot block the dashboard.

## Phase 1 — Model selection and execution observability

### Backend

- Add `GET /api/hermes/models` as a Cohub-authenticated proxy over `/api/model/options`.
- Add a typed model reference: `provider`, `model`, optional safe `model_options`, display label, capability hints, and pricing metadata.
- Extend workflow definitions with optional `default_model` and task nodes with optional `model`.
- Extend `POST /api/runs` with optional `model_override`.
- Persist the requested and effective provider/model on the run and external execution before submitting to Hermes.
- Send `provider`, `model`, and allowlisted `model_options` to `POST /v1/runs`.
- Capture Hermes status `model` and `usage`; show requested versus effective model when fallback occurs.
- Never accept arbitrary browser-provided providers or model IDs in the first release; validate against the current server-side catalog.

### UI

- Add a searchable model picker to New Run with provider grouping, capability badges, pricing hints, and a clear `Workflow default` option.
- Show the workflow recommendation and warn when a free/slow model is selected.
- Show effective model, duration, token usage, retries, and failure reason in Run Inspector.
- Add a workflow-level recommended model field; node-level selection arrives with the builder.

### Acceptance

- Selecting a model changes the exact Hermes `/v1/runs` payload.
- Restart resumes the existing external run with the persisted model snapshot.
- Old runs keep their original model metadata after catalog changes.
- Unavailable models fail before submission with an actionable message.
- Tests cover precedence, catalog validation, fallback reporting, redaction, persistence, and unsupported Hermes versions.

## Phase 2 — Visual workflow builder

### Draft model

- Add `workflow_drafts` with draft ID, name, description, editable definition, revision, validation state, timestamps, and optimistic concurrency version.
- Add draft CRUD, validate, duplicate-from-version, import, export, and publish endpoints.
- Publishing validates the draft, creates the existing immutable workflow version, and never mutates prior versions.

### Canvas

- Introduce an `@xyflow/react` workspace with a node palette, canvas, minimap, zoom controls, keyboard deletion, and explicit connection handles.
- Supported nodes:
  - **AI task:** prompt, output fields, model override, timeout, retries, side-effect marker.
  - **Decision:** named routes and output contract.
  - **Parallel:** direct one-step task branches only, matching the current engine boundary.
  - **Human approval:** review title, payload fields, and next step.
  - **End:** terminal state.
- Use a property inspector for forms instead of editing JSON.
- Persist layout metadata separately from the canonical executable definition so moving a card does not create a semantic version change.
- Prevent obvious invalid edges in the UI and return node-scoped backend diagnostics for all other validation failures.
- Keep Advanced JSON as import/export and troubleshooting; publishing should not require reading JSON.

### Acceptance

- A user can build, save, reopen, validate, and publish a linear workflow without touching JSON.
- Decision routes and parallel branches render as distinct labeled edges.
- Invalid cycles, dangling targets, missing end nodes, unsupported parallel subgraphs, and unsafe IDs cannot publish.
- Reloading or opening another browser preserves the draft.
- Existing JSON workflows can be cloned into editable drafts without changing their published fingerprint.
- Desktop and mobile tests cover node creation, editing, linking, validation, autosave, and publish confirmation.

## Phase 3 — Natural-language workflow generation

### Experience

- Add `Describe a workflow` beside `Build manually`.
- Ask for outcome, expected inputs, deliverables, approval points, side effects, and preferred model/cost profile.
- Show generation progress as a durable draft-generation run.
- Render the result immediately in the visual builder with a summary of assumptions and validation warnings.
- Support follow-up revisions such as `add approval before Telegram delivery` without publishing automatically.

### Safety and architecture

- Use a dedicated workflow-designer prompt containing the exact schema, node catalog, engine limits, and examples.
- Run generation with no executable tools. Prefer a request-scoped disabled toolset if Hermes exposes one; otherwise use a dedicated Hermes profile with tools disabled.
- Require strict JSON output, parse it as untrusted data, run the normal Cohub validator, and reject unknown keys or unsupported node types.
- Store generation prompt, selected model, Hermes run ID, candidate output hash, validation diagnostics, and revision lineage.
- Never auto-publish, auto-run, or grant approvals from generated output.
- Do not let generated workflows include secrets; detect likely credentials and require removal before saving.

### Acceptance

- Common Vietnamese and English requests produce a valid editable draft or actionable diagnostics.
- Malformed, unsupported, or tool-producing responses fail closed.
- Restart can recover a generation still in progress without creating a duplicate Hermes run.
- The user must review and explicitly publish the final draft.

## Phase 4 — Remove remaining JSON-first friction

- Add optional workflow `input_schema` and generate friendly New Run forms from it.
- Provide starter templates: research and summarize, daily briefing with approval, compare options, and prepare a deployment plan.
- Add reusable task presets only where they map to real engine behavior; do not present deterministic delivery nodes before adapters exist.
- Add draft version comparison and restore.
- Add a test-run mode with sample inputs and no side-effect adapters.
- Add cost and latency summaries by workflow/model to make model recommendations evidence-based.

## API and persistence sketch

```text
GET    /api/hermes/models
POST   /api/workflow-drafts
GET    /api/workflow-drafts/{id}
PATCH  /api/workflow-drafts/{id}
POST   /api/workflow-drafts/{id}/validate
POST   /api/workflow-drafts/{id}/publish
POST   /api/workflow-drafts/{id}/generate
POST   /api/workflow-drafts/{id}/revise
POST   /api/runs { workflow, version, input, model_override? }
```

New persisted data should include workflow drafts, draft revisions, generation records, run model snapshots, effective external model, usage, and layout metadata. Database migrations must be additive and safe for the existing production SQLite volume.

## Likely files

- `apps/cohub/cohub/db.py`: migrations, drafts, model snapshots, generation records.
- `apps/cohub/cohub/schemas.py`: model reference, input schema, draft diagnostics, node validation.
- `apps/cohub/cohub/executors.py`: model discovery, per-run routing, usage capture, generator operation.
- `apps/cohub/cohub/service.py`: durable generation reconciliation.
- `apps/cohub/cohub/api.py`: model proxy and draft/generation endpoints.
- `apps/cohub/frontend/App.tsx`: split into routes/features before adding the builder.
- `apps/cohub/frontend/features/models/`: model picker and catalog state.
- `apps/cohub/frontend/features/workflows/`: draft list, canvas, nodes, inspector, validation.
- `apps/cohub/frontend/features/generation/`: prompt wizard and revision flow.
- `apps/cohub/tests/` and `apps/cohub/frontend/e2e/`: contract, migration, routing, builder, and generation tests.
- `.github/workflows/ci.yml`, Dockerfile, README, architecture, and security documentation.

## Delivery strategy

Create separate PRs and deploy each capability before beginning the next:

1. **PR 1: Model catalog, selection, persistence, and usage.**
2. **PR 2: Durable workflow drafts and form-based node editor.**
3. **PR 3: Drag-and-drop canvas and labeled connections.**
4. **PR 4: Restricted Hermes prompt-to-workflow generation.**
5. **PR 5: Dynamic run-input forms, templates, and model analytics.**

This order delivers the immediate reliability improvement quickly, keeps migrations reviewable, and avoids coupling model routing, a graph editor, and LLM generation into one risky release.

## Quality gates for every PR

- TDD for schema, persistence, API, restart, and failure behavior.
- Vitest for reducers and graph transforms.
- Playwright desktop and mobile flows.
- Full Cohub and Lona suites.
- Python compile/type checks, npm audit, Vite build, wheel asset inspection, Compose validation, and image build in CI.
- Migration test against a copy of the current schema.
- Security scan proving no credentials or raw Hermes secrets enter API responses, logs, events, generated drafts, or Git.
- Rollback documentation before production deployment.

## Out of scope until these phases are stable

- Arbitrary cyclic graphs or general DAG joins beyond the current engine contract.
- Auto-publishing or auto-running generated workflows.
- Permanent Hermes approval scopes.
- Browser-to-Hermes direct access.
- Storing provider credentials in Cohub.
- A marketplace for third-party nodes.
