# DUTH-26: Workflow labels and review-skip labels for Lona Delivery

## Context and problem

The Lona Delivery workflow (DUTH-18) runs every PR through `code_review → security_review → qa → in_review`. Each step starts a paid agent run. Many changes, such as docs, copy or CI tweaks, gain nothing from a security pass or a QA pass. Those runs only cost tokens and add latency.

The `todo` classification gate already refers to the `needs-spec` and `fast-path` labels, but neither label exists in the workspace. No labels exist at all (`multica label list` returns `[]`).

The requester (duthaho) confirmed this scope on DUTH-26. The hourly autopilot, the Telegram alerts and auto-merge of `urgent` PRs are deferred to DUTH-27.

## Goals

- G1: Create the agreed label set in the workspace, so the existing `todo` gate labels (`needs-spec`, `fast-path`) actually work.
- G2: Add two labels that let an issue skip review steps that are not needed: `skip-security` and `skip-qa`.
- G3: Define the rules for these labels and write them everywhere agents read them: the agent instructions of the review agents (Agent Identity takes priority over step instructions), the live workflow step instructions, the `lona-delivery-conventions` skill and `config/multica/team.json`.
- G4: Make the label set reproducible. Declare the labels in `config/multica/team.json` and seed them with the existing team setup script, without overwriting live customization.

## Non-goals

- The hourly autopilot, stale-task alerts, Telegram integration and auto-merge (DUTH-27).
- Any behavior for `urgent` beyond creating the label. Its auto-merge meaning is defined in DUTH-27.
- Skipping `code_review`. Every PR is still reviewed by Trung-PM.
- Skipping human acceptance (`in_review`).
- Conditional routing in the Multica platform itself. The workflow has a single `next_status_key` per step, so skips are carried by step instructions (see Constraints).

## Users and use cases

- **duthaho (requester / workspace owner):** tags an issue `skip-qa` for a docs-only change, so it goes from code review straight to human acceptance.
- **Phương-CTO (triage):** at `todo`, or when writing the plan, adds `skip-security` and/or `skip-qa` when the change clearly qualifies, and adds type labels.
- **Trung-PM (code review):** checks against the actual diff that each skip label is still valid, then routes to the right next step.
- **Hoàng-Security / Diễm-QA:** are not started for issues that legitimately skip their step.

## Label set

| Label | Color | Description |
|---|---|---|
| `needs-spec` | `#8b5cf6` | Force the `todo` gate to send the issue to `spec`. |
| `fast-path` | `#22c55e` | Skip the `todo` classification gate. |
| `urgent` | `#ef4444` | High urgency. Auto-merge semantics are defined in DUTH-27; there is no behavior yet. |
| `skip-security` | `#f59e0b` | Skip `security_review`, subject to the eligibility rules below. |
| `skip-qa` | `#f59e0b` | Skip `qa`, subject to the eligibility rules below. |
| `bug` | `#dc2626` | Type: defect fix. |
| `feature` | `#3b82f6` | Type: new user-facing behavior. |
| `chore` | `#6b7280` | Type: maintenance, refactor, dependency bumps. |
| `docs` | `#06b6d4` | Type: documentation only. |
| `infra` | `#0ea5e9` | Type: CI/CD, Docker, deployment, Multica config. |
| `security` | `#b91c1c` | Type: security fix or hardening. |

Colors are a proposal. The implementer may adjust them, but the names are fixed.

## Skip-label rules

**Who may add them:** duthaho, or Phương-CTO during `todo` triage or the `plan` step. Specialists and reviewers do not add skip labels. Trung-PM may only remove them (see AC6). Once Trung-PM has removed a skip label, only duthaho may add it back.

**Authorization comment (required).** Label changes have no ACL and are not in the issue timeline. So whoever adds a skip label must also post a comment on the issue in this form:

```
skip-security: <reason>; head <sha>
skip-qa: <reason>; head <sha>
```

A skip label counts only when such a comment exists and its author is duthaho or Phương-CTO. Without it, Trung-PM removes the label and routes normally.

**`skip-security` eligibility: allowlist, fail-closed.** `skip-security` is valid only when **every** file in `gh pr diff --name-only` matches the allowlist below:

- `docs/**/*.md` and `README.md`
- other `*.md` files, **except** `SKILL.md`, `SOUL.md`, and anything under `.claude/**` or `config/**`
- `tests/**`, as long as no fixture contains credentials or credential-shaped values

If any file falls outside the allowlist, or the reviewer is not sure, the label is removed. Comment-only edits in code files (`.py`, `.ts`, …) are code changes and are not eligible.

The following are never eligible, whatever the file type. They are listed to explain the allowlist; the allowlist is what decides:

- **Agent control and policy surfaces:**
  - `config/multica/**`, `config/hermes/**`, `config/openclaw/**` and `.claude/**`
  - any `SKILL.md` or `SOUL.md`
  - agent instructions and workflow step text
  - any file that defines agent permissions, tools or model routing

  These are the review rules and system prompts themselves.
- `deploy.sh`, `scripts/**`, `.env.example`, `.gitignore`, `.dockerignore` and `.github/**` (workflows, CODEOWNERS, dependabot).
- Dockerfiles, Compose files and lockfiles.
- New or upgraded dependencies.
- **Security-sensitive code:**
  - auth and authz, secrets and credentials, crypto, payments
  - logging that could leak secrets
  - outbound HTTP (SSRF)
  - file upload and path handling
  - deserialization and DB access
  - LLM prompt and tool-call handling
  - Telegram handlers
- Network exposure (ports, proxies, webhooks).
- Committed frontend build output (`apps/cohub/cohub/static/**`).
- Any issue labelled `security` or `infra`.

**`skip-qa` is not allowed** in either of these cases:

- the change alters user-facing behavior (`feature`, or `bug` with a visible fix);
- the diff touches any file outside `docs/`, `tests/`, `*.md`, `.claude/skills/**` and the skill/workflow text in `config/multica/team.json`.

Trung-PM checks this against `gh pr diff --name-only`.

**Typical eligible cases:**

- `skip-security`: docs-only and test-only changes.
- `skip-qa`: docs-only changes, Multica workflow or skill text, and test-only changes. Tests must still be green at code review.

**Checked SHA, so later commits are not missed.** Trung-PM's verdict records the PR head SHA at which the skip labels were checked. A skip label is valid only for that SHA. If any commit is pushed after it, the skip lapses and the issue goes back to `code_review`. Before merging, the merger compares the PR head with the SHA in the verdict.

**Visibility at acceptance.** When an issue reaches `in_review` with a step skipped, the hand-off comment must say so explicitly: `security_review skipped (label added by X, checked at SHA Y)`, and the same for `qa`. That way duthaho knows the PR did not get that review.

**Routing after `code_review` APPROVE:**

- no skip labels → `security_review` (unchanged)
- `skip-security` only → `qa`
- `skip-qa` only → `security_review`, then on pass → `in_review`
- both → `in_review`

## Acceptance criteria

- AC1: Given the workspace, when `multica label list` runs, then all 11 labels in the table above exist, each with the table's Description column as its label description.
- AC2: Given `config/multica/team.json` declares the labels, when the team setup script (`config/multica/seed-team.sh` → `scripts/multica_team.py`) runs against a workspace missing some of them, then it creates only the missing labels. It leaves existing labels (including edited colors and descriptions) untouched, and a second run makes no changes. This is covered by tests in `tests/test_multica_team.py`.
- AC3: Given an approved PR on an issue with `skip-security` and no `skip-qa`, when Trung-PM finishes code review, then the issue moves to `qa` and no Hoàng-Security run starts.
- AC4: Given an approved PR on an issue with both skip labels, when Trung-PM finishes code review, then the issue moves to `in_review`.
- AC5: Given an issue with `skip-qa` that passes security review, when Hoàng-Security finishes, then the issue moves to `in_review` and no Diễm-QA run starts.
- AC6: Given an issue with `skip-security` where any of the following holds:
  - a file in the diff falls outside the allowlist;
  - there is no authorization comment;
  - the authorization comment's author is not duthaho or Phương-CTO,

  when Trung-PM reviews it, then Trung-PM removes the label, states why in the verdict, and routes to `security_review`. The same applies to `skip-qa` and `qa`.
- AC10: Given an issue with a skip label and a commit pushed after the SHA recorded in Trung-PM's verdict, when the issue reaches the next step or merge, then it is routed back to `code_review`.
- AC11: Given an issue that reaches `in_review` with a skipped step, when the hand-off comment is posted, then it states which step was skipped, who added the label, and the checked SHA.
- AC7: Given the live Lona Delivery workflow, when `multica workflow get "Lona Delivery"` runs, then the `code_review` and `security_review` step instructions contain the routing rules above. The `todo` (and `plan`) instructions state who may add skip labels and when.
- AC8: Given the live agents, when `multica agent get` runs for Trung-PM, Hoàng-Security and Diễm-QA, then:
  - Trung-PM's instructions allow removing an invalid skip label and routing by skip labels after APPROVE (no hard-coded "APPROVE → `security_review`" left, no "do not route issues" conflict).
  - Trung-PM's instructions require the authorization comment check and the checked head SHA in the verdict.
  - Hoàng-Security's instructions route a pass to `in_review` when the issue has `skip-qa`.
  - Diễm-QA's instructions do not conflict with the skip rules.
  - The live instructions match `agents[].instructions` in `config/multica/team.json`.
- AC9: Given the repo, when the implementation PR is merged, then the `lona-delivery-conventions` content matches in three places: `skills[]` in `config/multica/team.json`, the workspace skill (`multica skill get lona-delivery-conventions --with-content`), and the workflow steps in `team.json` match `multica workflow get "Lona Delivery"`.

## Constraints

- Multica workflows have one fixed `next_status_key` per step and no label conditions. The skip is done by the step handler moving the issue to the later status with `multica issue status`, as its instructions say. This was verified with `multica workflow update --help`, which has no condition flags.
- `config/multica/team.json` is also changed by the open PR #26 (DUTH-18). The implementation must be based on `main` after #26 merges, or be rebased onto it, so the DUTH-18 steps (`spec_review`, `plan`, Trung-PM as code reviewer) are not reverted.
- The team setup script currently has no label support (`grep -i label scripts/multica_team.py` is empty). Adding it is a code change and follows the normal review path. It must use the same create-missing-only policy as the other assets.
- **DUTH-27 interaction:** an issue with `skip-security` must never be auto-merged. The auto-merge rules in DUTH-27 must exclude it (`skip-security` combined with `urgent` means no auto-merge). Otherwise Phương-CTO could add the label and also merge, and no human or security reviewer would see the PR.
- Updating the live workflow is a shared-environment change. It is applied only after the plan is approved by the requester.

## Risks

- **The change loosens a security control:** `skip-security` bypasses the security review gate. Hoàng-Security must review and approve the `skip-security` ineligible list during the `plan` step, before any implementation starts.
- **Agent Identity overrides step instructions:** if only the step text changes, the reviewers' identities (for example, Trung-PM's "APPROVE → `security_review`") would win and the skip would never happen. Mitigation: AC8 updates the agent instructions both live and in `team.json`.

- **A skip label is misapplied and a risky change misses security or QA.** This can happen by mistake or through prompt injection. Mitigations:
  - a fail-closed path allowlist;
  - an authorization comment from duthaho or Phương-CTO;
  - a re-check by Trung-PM against the diff, recorded with the head SHA (AC6, AC10);
  - an explicit skipped-step note at human acceptance (AC11).
- **Instructions are routing logic and agents may not follow them.** Mitigation: explicit, ordered routing rules in the step text, plus AC3–AC5 verified on a real test issue during QA.
- **The live workflow and `team.json` drift apart.** Mitigation: AC8 and AC9, and the PR exports the live workflow after the update.

## Open questions

- None open. The requester's decisions are recorded in the DUTH-26 comment thread. Who may add skip labels is a default chosen here; the requester can change it when approving the plan.

## Rollout and rollback

**Rollout:**

1. Merge this spec. At `plan`, Hoàng-Security's review (round 1: REQUEST CHANGES, comment on DUTH-26) is applied in this revision:

- **H1, agent control surfaces were eligible:** removed. `config/**`, `.claude/**`, `SKILL.md`/`SOUL.md` and agent or workflow text are never eligible for `skip-security`.
- **H2, denylist replaced by allowlist:** `skip-security` now uses a fail-closed path allowlist. The denylist stays only as explanation, and issues labelled `security` or `infra` can never skip.
- **H3, adding the label was not enforced:** the authorization comment is now required, with an author check (AC6). Only duthaho may re-add a label that Trung-PM removed.
- **H4, TOCTOU:** the checked head SHA is recorded in the verdict. Any later push sends the issue back to `code_review`, and the merger checks the SHA (AC10).
- **M1, comment-only code edits:** no longer eligible.
- **M2, overlap with DUTH-27:** no auto-merge for `skip-security` issues (Constraints), and a skipped-step note is required at `in_review` (AC11).
- **Recommendation, not blocking:** a weekly post-hoc audit of merged `skip-security` PRs. Carried to DUTH-27.

Trade-off: `skip-security` now only saves runs on docs-only and test-only changes. Workflow and skill text changes still skip QA but not security.

### Sub-issues

One implementation issue: all changes ship in one PR and are verified end to end together.

**Stage 1, `todo`: Implement workflow labels and skip-review routing**

- **Handler:** Đà-DevOps (Multica config and team setup script; same owner as DUTH-7).
- **Goal:** G1–G4.
- **Acceptance criteria:** AC1–AC9 of this spec.
- **Dependencies:**
  - PR #26 (DUTH-18) must be merged first: the branch is based on `main` after #26.
  - This spec PR (#27) must be merged.
- **Review path:** normal (`code_review → security_review → qa`); no skip labels on this issue itself.
