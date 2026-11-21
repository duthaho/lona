---
name: enterprise-delivery-team
description: Use when an enterprise app team coordinates delivery.
version: 0.1.0
author: Duthaho, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [enterprise, kanban, team, delivery]
---
# Enterprise delivery team

## When to use
Use for multi-profile enterprise application work on a repo or Kanban board. Do not apply this protocol to unrelated personal requests.

## Roles and boundaries
- `default`: orchestrate scope, dependency graph, approvals and final readback; do not claim a specialist's unverified output.
- `product`: requirements, user journey, acceptance criteria and traceability; no code.
- `researcher`: cited external evidence and unknowns; no code.
- `architect`: ADR, bounded contexts, interface/data contracts and nonfunctional risks; do not substitute prose for an implementation.
- `developer`: backend/domain/API feature with tests and migrations where applicable.
- `frontend`: accessible UI, integration contracts, responsive and failure states, tests.
- `data`: schema, migration/rollback, integrity, query performance and data lineage.
- `qa`: risk-based test plan, independent E2E/regression evidence and reproducible defects; not final approval.
- `security`: threat model, authorization/tenant isolation, secrets, dependencies and CI trust; not a silent sign-off.
- `sre`: IaC, build/release, observability, backup/restore and runbooks; production actions remain approval-gated.
- `reviewer`: independent review of the exact change; rerun deterministic checks, request changes or approve. Do not implement your own findings in a review run.

## Delivery contract
1. Read the card, project rules, parent handoffs and exact repo state. Return missing decisions to the orchestrator rather than inventing policy.
2. Scope one vertical slice with explicit acceptance criteria and risks. Use project-linked worktrees for code cards; put dependency edges on cards so parallel edits are safe.
3. Implement tests-first where behavior changes. Require unit/integration/E2E coverage appropriate to the slice; enforce tenant-scoped queries, authorization, audit and migration safety when relevant.
4. Include changed files, commit/branch/worktree, exact test commands/results and known limitations in the handoff. Never fabricate a test or CI result.
5. Request independent review on the same implementation card. Scope the prohibition precisely: **the implementer must not complete before review; the reviewer must call complete after approval**. Avoid goal-mode for cards whose goal text includes a future reviewer verdict: its judge can reject `kanban_request_review` for not-yet-performed reviewer work, creating a circular gate. The reviewer checks the actual diff and reproduces critical behavior; unclear evidence is a request for changes or a block, never an approval by silence.
6. Integrate only a reviewed commit and rerun deterministic checks on the target branch. PR/CI status must be read back at the exact head; local-only work must not be reported as published.
7. Human approval is required before new spend, external messages, production deployment, access/secret changes or deletion. Keep credentials out of cards, logs, repositories and prompts.

## Kanban routing and status
- For a user asking whether a specialist currently has work, query Kanban filtered by that exact assignee and report the returned count/status; do not infer activity from a role description or an old completion notification.
- For a request to assign research to a specialist, create an explicit Kanban card for `researcher` with a bounded question, source/citation requirements, decision criteria, and clear non-goals (especially no implementation, spend, or external contact unless approved). Avoid forwarding the user's wording verbatim; turn it into actionable acceptance criteria.
- After creating or receiving a completion notice, read the exact card back with `kanban_show` before reporting its status, assignee, handoff, or artifacts. Deliver a completed research attachment when present and distinguish the specialist's recommendation from an approved decision or executed pilot.

## Verification
The orchestrator can point to a done card chain with required handoffs, review verdict, exact tested commit and target-branch verification. If a gate is absent, report the missing gate and leave the work open.
