---
name: github-actions-trust-boundaries
description: "Use when building trusted GitHub Actions policy workflows."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
---

# Trusted GitHub Actions Delivery

Use this skill for GitHub Actions workflows that publish checks, enforce merge policy, or create releases—especially where PR content is untrusted.

## Trust-boundary design

1. Treat PR code, diffs, workflow edits, and repository instructions as untrusted input.
2. For privileged post-review publication, use `workflow_run` from a workflow present on the default branch. Do not use `pull_request_target` to check out or execute PR code.
3. Apply least-privilege permissions explicitly. A check publisher normally needs `checks: write`, `contents: read`, and possibly `pull-requests: read`; a release job needs `contents: write` only when creating a release.
4. Keep provider credentials out of privileged publishers. If a job needs trusted framework code, explicitly check out the default branch, never the PR head.
5. Pin all third-party actions to immutable commit SHAs. Prefer built-in GitHub CLI/API calls over an unpinned release action when practical.

## Workflow-run acceptance procedure

A newly added `workflow_run` workflow cannot self-validate on the PR that introduces it: GitHub resolves its definition from the default branch.

1. Merge the workflow PR only after normal CI and required human approval.
2. Create a minimal acceptance PR from the updated default branch. It should change only a clearly temporary fixture.
3. Inspect check runs for the exact acceptance head SHA. Confirm both the named published check and its `status`, `conclusion`, and summary.
4. A `neutral`/`failure` result caused by a deliberately skipped or missing prerequisite is a valid fail-closed enforcement result—not a successful merge-gate result.
5. Close the temporary PR without merging. Read it back through the API and confirm `state=closed` and `merged=false`.

## Merge-policy workflow rules

- Query the exact PR head SHA, changed paths, check runs, review state, and managed finding threads.
- Fail closed on pagination, malformed API shapes, missing required checks, incomplete reviews, or unavailable findings.
- Evaluate the latest review state per reviewer; an earlier approval must not survive a later `CHANGES_REQUESTED` state.
- Publish policy checks on the reviewed head SHA. Do not declare the policy passing solely because the Actions job succeeded.
- Document which human signals are trusted (for example, a maintainer-managed attestation label and non-author approval) and do not infer stronger authorization than GitHub data provides.

## Release workflows

Use `workflow_dispatch` with an explicit tag input. Before release creation:

1. Verify the requested tag equals the package version.
2. Install dependencies without lifecycle scripts where possible.
3. Run the complete project verification suite.
4. Build and pack the exact artifact to upload.
5. Create the release/tag only after those gates succeed.

Releasing, tagging, or publishing is irreversible external state: require explicit user authorization immediately before dispatching it, even if release automation is already merged.

## Required verification

- Add regression tests that inspect workflow files for trigger, permissions, SHA-pinned actions, default-branch checkout, credential absence, and release checks.
- Run the full project quality gate and `git diff --check` before PR creation.
- After every GitHub state mutation—PR creation, closure, merge, release—read back the exact API target before reporting success.
