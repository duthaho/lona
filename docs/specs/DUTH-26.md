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

**Who may add them:** duthaho, or Phương-CTO during `todo` triage or the `plan` step. Specialists and reviewers do not add skip labels. Trung-PM may only remove them (see AC6).

**`skip-security` is not allowed** when the diff touches any of the following:

- auth or authz
- secrets or credentials handling
- crypto
- payments
- input parsing exposed to users or external systems
- new or upgraded dependencies
- Dockerfiles, Compose files or CI workflows
- network exposure (ports, proxies, webhooks)
- the issue has the `security` label

**`skip-qa` is not allowed** when the change alters user-facing behavior (`feature`, or `bug` with a visible fix), or when the diff touches any file outside `docs/`, `tests/`, `*.md`, `.claude/skills/**` and the skill/workflow text in `config/multica/team.json`. Trung-PM checks this against `gh pr diff --name-only`.

**Typical eligible cases:** docs-only, comments/typos, Multica workflow or skill text, and test-only changes (`skip-qa` still needs green tests at code review).

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
- AC6: Given an issue with `skip-security` whose diff touches an ineligible area, when Trung-PM reviews it, then Trung-PM removes the label, states why in the verdict, and routes to `security_review`. The same applies to `skip-qa` and `qa`.
- AC7: Given the live Lona Delivery workflow, when `multica workflow get "Lona Delivery"` runs, then the `code_review` and `security_review` step instructions contain the routing rules above. The `todo` (and `plan`) instructions state who may add skip labels and when.
- AC8: Given the live agents, when `multica agent get` runs for Trung-PM, Hoàng-Security and Diễm-QA, then:
  - Trung-PM's instructions allow removing an invalid skip label and routing by skip labels after APPROVE (no hard-coded "APPROVE → `security_review`" left, no "do not route issues" conflict).
  - Hoàng-Security's instructions route a pass to `in_review` when the issue has `skip-qa`.
  - Diễm-QA's instructions do not conflict with the skip rules.
  - The live instructions match `agents[].instructions` in `config/multica/team.json`.
- AC9: Given the repo, when the implementation PR is merged, then the `lona-delivery-conventions` content matches in three places: `skills[]` in `config/multica/team.json`, the workspace skill (`multica skill get lona-delivery-conventions --with-content`), and the workflow steps in `team.json` match `multica workflow get "Lona Delivery"`.

## Constraints

- Multica workflows have one fixed `next_status_key` per step and no label conditions. The skip is done by the step handler moving the issue to the later status with `multica issue status`, as its instructions say. This was verified with `multica workflow update --help`, which has no condition flags.
- `config/multica/team.json` is also changed by the open PR #26 (DUTH-18). The implementation must be based on `main` after #26 merges, or be rebased onto it, so the DUTH-18 steps (`spec_review`, `plan`, Trung-PM as code reviewer) are not reverted.
- The team setup script currently has no label support (`grep -i label scripts/multica_team.py` is empty). Adding it is a code change and follows the normal review path. It must use the same create-missing-only policy as the other assets.
- Updating the live workflow is a shared-environment change. It is applied only after the plan is approved by the requester.

## Risks

- **The change loosens a security control:** `skip-security` bypasses the security review gate. Hoàng-Security must review and approve the `skip-security` ineligible list during the `plan` step, before any implementation starts.
- **Agent Identity overrides step instructions:** if only the step text changes, the reviewers' identities (for example, Trung-PM's "APPROVE → `security_review`") would win and the skip would never happen. Mitigation: AC8 updates the agent instructions both live and in `team.json`.

- **A skip label is misapplied and a risky change misses security or QA.** Mitigation: a narrow eligibility list, only the requester or Phương-CTO may add the labels, and Trung-PM re-checks against the diff and removes invalid labels (AC6). Human acceptance is still required.
- **Instructions are routing logic and agents may not follow them.** Mitigation: explicit, ordered routing rules in the step text, plus AC3–AC5 verified on a real test issue during QA.
- **The live workflow and `team.json` drift apart.** Mitigation: AC8, and the PR exports the live workflow after the update.

## Open questions

- None open. The requester's decisions are recorded in the DUTH-26 comment thread. Who may add skip labels is a default chosen here; the requester can change it when approving the plan.

## Rollout and rollback

**Rollout:**

1. Merge this spec. At `plan`, Hoàng-Security reviews the `skip-security` ineligible list.
2. Implementation PR: label seeding in `multica_team.py` with tests, the label declarations, the updated workflow step text, and the updated `lona-delivery-conventions` in `team.json`.
3. After approval, run the team setup script to create the labels, then apply the step instructions with `multica workflow handoff set`, and update the agent instructions of Trung-PM, Hoàng-Security and Diễm-QA with `multica agent update`.
4. Verify AC1, AC3–AC5 on a test issue.

**Rollback:** restore the previous step instructions and agent instructions with `multica workflow handoff set` from the `main` version of `team.json`, and remove the skip labels from any open issues. Labels can stay; without the instructions they have no effect.

## Plan

(added at the `plan` step)
