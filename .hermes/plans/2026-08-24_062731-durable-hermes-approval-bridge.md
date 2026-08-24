# Durable Hermes Approval Bridge

## Goal
Bridge Hermes Runs API tool approvals into Cohub's durable, payload-bound approval state machine without disabling Hermes security or duplicating external runs.

## Architecture

- Add a SQLite `external_executions` record keyed by Cohub run/step/attempt.
- Split Hermes execution into submit, reconcile, approval-response, and stop operations.
- Persist the Hermes run ID immediately after submission.
- Read `approval.request` details from the Hermes SSE events endpoint; polling state alone is not sufficient.
- Persist the redacted event as a Cohub approval payload and release the worker lease.
- Map Cohub approve/reject to Hermes `once`/`deny`, then requeue the same Cohub step and reconcile the same Hermes run.
- Treat duplicate/already-resolved external approval responses idempotently.
- Stop active Hermes runs when Cohub is cancelled or when reconciliation times out.
- Fail closed when approval details cannot be recovered; never invent a review payload.

## TDD slices

1. External execution persistence and restart lookup.
2. Hermes Runs client submit/status/SSE/approval/stop contract.
3. Worker creates exactly one durable approval and releases its lease.
4. Approval resolution resumes the same Hermes run for both `once` and `deny`.
5. Cancellation and timeout propagate `/stop`.
6. Restart does not submit a duplicate external run.
7. Failure envelope without `output` preserves failed status.
8. API end-to-end approval bridge and full regression suite.

## Acceptance criteria

- A read-only Hermes run completes without creating an approval.
- `waiting_for_approval` creates one payload-hashed Cohub approval containing only Hermes-redacted review data.
- Approve/reject resumes the original Hermes run ID.
- Duplicate resolve is safe.
- Restart while waiting does not resubmit.
- Cohub cancellation/timeout stops Hermes.
- Missing SSE detail fails closed.
- All Lona and Cohub tests, package build, JavaScript syntax, and GitHub CI pass.
