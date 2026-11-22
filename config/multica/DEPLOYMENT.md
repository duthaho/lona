# Multica deployment and recovery limitations

Use `./deploy.sh multica up` to generate missing secrets and start the stack; `./deploy.sh multica config` edits `.env` and `up` applies it. Keep `.env` private and retain a secure copy: `MULTICA_VCS_SECRET_KEY` encrypts existing VCS tokens, and changing or losing it can make them unreadable. It must be canonical base64 encoding of exactly 32 random bytes (`openssl rand -base64 32`). An invalid configured key is rejected, not rotated. A pre-existing invalid key requires operator investigation and a deliberate recovery decision; do not replace it blindly.

On `up`, exported VCS and JWT secrets are persisted if absent from `.env`; existing values must match exports and are never silently replaced. JWT exports being newly persisted must contain only ASCII letters, digits, `.`, `_`, `~`, or `-`; otherwise set the value durably in `.env` first. `update` rejects exports that do not match `.env`. Protect `.env` before running.

`MULTICA_POSTGRES_PASSWORD` must contain only ASCII letters, digits, `.`, `_`, `~`, or `-` (URI-unreserved characters). The generated hex password meets this requirement. On first run, a valid exported password is saved to `.env` if it has no password; if both are set they must match, otherwise deployment stops without changing either. Protect `.env` before running. The backend refuses other characters before starting, rather than silently connecting with a malformed URI; Postgres may already have started. Changing this setting does **not** change the password in an existing PostgreSQL volume: coordinate any deliberate credential change with the database and retain recovery access. The guard relies on the upstream backend image's `/bin/sh`, `/app` working directory, and `./entrypoint.sh` contract; verify these when changing images.

Compose services are isolated to avoid unrelated profile secrets being required. Direct commands are:

```sh
docker compose --profile openclaw config -q
docker compose -f docker-compose.yml -f docker-compose.hermes.yml --profile hermes config -q
docker compose -f docker-compose.multica.yml --profile multica config -q
```

All use project `lona` and the same bridge MTU. The existing `lona_multica-pgdata` and `lona_multica-uploads` volume names are retained. Do not use `down -v` unless intentionally destroying data.

`./deploy.sh multica backup` is **unsupported** and fails before creating an archive. Multica persists PostgreSQL and uploads in Docker volumes, not `data/multica`; ordinary Lona archives do not capture them. No tested backup or restore procedure is provided here. Arrange and verify independent database, upload-volume, and secret recovery before relying on this deployment.

`./deploy.sh multica update` pulls the three images and runs `compose up -d --wait`. This is **not transactional**: there is no automatic backup or rollback, and image upgrades or database migrations may be irreversible. Pin reviewed image versions, establish a tested recovery plan, and review upstream migration guidance before updating. Pull/start failures are propagated; `--wait` does not prove end-to-end functionality. Scheduled update arguments are unsupported.

Multica `doctor`, `cli`, and `sync` are unsupported and fail explicitly. Inspect `status` and `logs`; use the host `multica` CLI, and use `team-setup` for explicit team reconciliation. `install-agents` only installs binaries. It does not authenticate a provider or establish a runtime. The printed interactive `multica setup self-host --server-url … --app-url …` changes CLI connection/auth configuration and starts a daemon; review the existing configuration and target first. The command and `multica daemon status` are documented in upstream SELF_HOSTING.md. Do not run setup automatically against existing credentials.

Local model validation does not start containers. A Docker daemon is needed to verify actual service readiness, migrations, persistent volume upgrades, and recovery.
