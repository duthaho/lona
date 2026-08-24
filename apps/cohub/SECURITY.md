# Security Policy

## Security model

Cohub assumes a trusted single operator and is local-first by default.

- The server binds to `127.0.0.1` unless explicitly changed.
- Configure `COHUB_API_TOKEN` before exposing the API through a tunnel or reverse proxy.
- Use Cloudflare Access, Tailscale, or an equivalent identity-aware proxy for remote access.
- Keep provider credentials in Hermes. Do not put secrets in workflow documents.
- Protected actions use a canonical JSON payload and SHA-256 hash. Approval fails if the reviewed hash does not match the pending payload.
- Workflow definitions and run fingerprints are immutable after publication.
- Artifact paths are resolved and checked against the configured artifact root.
- API request bodies are limited to 1 MB.
- Static paths are containment-checked.
- Worker leases prevent ordinary duplicate execution, but external writes must still use provider idempotency or reconciliation.
- Hermes tool approvals remain enabled independently of Cohub workflow approvals. Cohub exposes only one-shot approval or denial and persists only Hermes-redacted review fields.

## Important limitations

- SHA-256 proves payload identity, not that the action is safe.
- Plugin capabilities are consent and audit boundaries, not a sandbox.
- The standard-library HTTP server is suitable behind a trusted reverse proxy, not as a public edge server.
- The MVP local executor is for demos and tests. It does not provide process isolation.
- Arbitrary workflow prompts are untrusted input to the configured agent and tools.
- SQLite and artifact directories contain potentially sensitive operational data and must be protected by filesystem permissions and backups.

## Production checklist

1. Bind Cohub only to loopback or a private interface.
2. Set a long random `COHUB_API_TOKEN`.
3. Put TLS and identity-aware access control in front of Cohub.
4. Configure Hermes tool approval and sandbox policies independently.
   Never use global YOLO as a substitute for the approval bridge.
5. Require approval for email, messaging, GitHub writes, deploys, deletion, infrastructure, and payments.
6. Give every external write a stable idempotency key and implement read-back reconciliation.
7. Back up SQLite through its backup API or while Cohub is stopped; never copy WAL files independently.
8. Monitor failed runs, expired leases, and pending approvals.
9. Rotate Hermes and Cohub tokens periodically.

## Reporting vulnerabilities

Please use a private GitHub security advisory for the repository. Do not include credentials, production payloads, or personal artifacts in a public issue.
