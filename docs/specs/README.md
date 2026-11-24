# Specs

Product specs for large or ambiguous issues, one file per issue: `<ISSUE-KEY>.md`.

Lore writes a spec at the `spec` step of the Lona Delivery workflow and opens it as
its own PR titled `<ISSUE-KEY>: Spec <topic>`. Trung-PM reviews it at `spec_review`.
At `plan`, Lore adds a `## Plan` section, and the requester approves it on the
issue before implementation starts. Implementation PRs link back to the spec.

The template and the review checklist are in the `lona-delivery-conventions` and
`lona-review-checklist` skills (`config/multica/team.json`).
