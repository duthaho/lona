---
name: credential-persistence
description: "Use when credentials must persist across agent sessions."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [credentials, secrets, authentication, persistence, security]
---

# Credential Persistence

## Goal

Establish one durable, least-privilege credential path for agent work without copying secrets into source, logs, reports, or chat. A successful one-off OAuth operation is not persistent authentication.

## Workflow

1. **Discover an existing credential before asking the user to authenticate.** Check the relevant CLI store, injected environment variable, secure agent environment file, and platform credential helper in that order.
2. **Validate without printing the secret.** Call the provider identity/whoami endpoint and print only the authenticated account and non-sensitive scope/status metadata.
3. **Persist only after validation.** Prefer the provider CLI's secure credential store. If an agent environment must provide the credential to new sessions, write one explicit variable into its environment file with owner-only permissions (`0600` on Unix).
4. **Verify the durable path in a fresh shell/session.** Source or start the target environment anew and repeat the non-sensitive identity check.
5. **Use the credential only through variables or credential stores.** Never interpolate a token in a command that prints it, repository remote URL, committed `.env`, fixture, PR text, or issue comment.
6. **Rotate exposed credentials.** If a token was pasted into a public or group conversation, recommend revocation and replacement after the immediate task; do not repeat it in the response.

## Device OAuth Rule

Device-code approval grants a token to the polling process, not automatically to later processes. Immediately place a successfully validated token into the selected durable credential mechanism and verify it. Do not start another device flow until all existing credential locations have been checked.

## GitHub Pattern

For GitHub API work, validate with `GET /user` using `Authorization: Bearer $GITHUB_TOKEN`, output only the login, then store `GITHUB_TOKEN` in the selected secure mechanism. The token must have only the scopes/permissions required by the intended repository work. New Hermes sessions normally require a gateway/session restart to inherit a newly written environment variable.

## Completion Criteria

- The desired provider identity has been verified without revealing a secret.
- A fresh shell/session can authenticate through the durable path.
- The credential file/store permissions are restricted.
- No credential was written to repository-tracked files or surfaced in reports.
