# Portable Hermes enterprise team

`./deploy.sh hermes up` seeds the **default orchestrator plus ten specialist profiles** from `config/hermes/profiles/` into `data/hermes/profiles/`. The manifest `config/hermes/team/roles.json` declares each role's bundled skills. Its 16 curated, general-purpose skill directories are checked into the repo, including their supporting files; no network skill install is required for the team. The Docker bind mount maps `data/hermes` to Hermes' `/opt/data`.

| Role | Responsibility | Reasoning |
|---|---|---|
| default | Orchestration, dependencies, integration, human approval | medium |
| product | Requirements, journeys, acceptance criteria | medium |
| researcher | Cited external research | medium |
| architect | ADRs, API/data boundaries | high |
| developer | Domain/backend/API and tests | medium |
| frontend | Accessible UI and integration | medium |
| data | Schema, migrations and integrity | high |
| qa | Independent E2E and regression | medium |
| security | Threat model, auth, secrets and CI trust | high |
| sre | Infrastructure, CI/CD, observability and recovery | medium |
| reviewer | Independent same-card review | high |

The gateway serves all profiles with `multiplex_profiles: true`. Deployment creates the empty `enterprise-apps` Kanban board when the Hermes container is running; existing boards and cards remain runtime data. Use explicit Kanban cards, owners, dependencies and project/repo bindings for team work. The implementer requests independent review **on the same card**; the reviewer checks the actual diff/tests before completing it. Avoid goal-mode cards whose completion criteria require a future reviewer verdict: that can block the handoff itself.

## Fresh installation

1. Clone the repo, configure `.env` through the usual setup, then run `./deploy.sh hermes up`.
2. Check `./deploy.sh hermes cli profile list` and `./deploy.sh hermes cli kanban boards list --json`. The latter should include `enterprise-apps` (deployment warns if the running image cannot create it).
3. Check local seeded assets: `python3 scripts/hermes_team.py check`. In the container, check e.g. `./deploy.sh hermes cli -p architect config get agent.reasoning_effort` and `./deploy.sh hermes cli -p reviewer skills list --enabled-only`.
4. Create the project/worktree and cards explicitly. The board is not automatically selected and no sample/demo card is imported.

`up` only creates missing files: repeat runs preserve live profiles, model choices, role instructions, credentials and skills. `./deploy.sh hermes sync` applies checked-in root config/SOUL and reconciles profile policy and **only previously Lona-managed** role instructions/skills; it preserves modified role instructions, existing model/provider, extension keys and nonempty live Telegram topic routes. `sync` needs host Python 3 with PyYAML (`python3 -m pip install PyYAML` in a virtual environment if your OS manages Python packages); `up` does not. Review `backups/` and the live config before sync on an existing deployment.

## Environment-specific setup and limits

- The templates default to Lona's OpenRouter free-model chain. **They do not replicate the private `openai-codex` subscription login or GPT model IDs of another machine.** To match that setup, authenticate OpenAI Codex separately with Hermes on the new host, then set the desired model/provider in `data/hermes/config.yaml` and each specialist's `data/hermes/profiles/<role>/config.yaml`. Those live model choices survive subsequent team syncs; the root model follows the ordinary Lona root-config sync policy. Do not commit OAuth tokens or auth state.
- No Telegram group/topic IDs, bot tokens or routes are shipped. Configure them in the live `data/hermes/config.yaml` only after creating/verifying them on that installation. Specialist Kanban workers do **not** require one bot or Telegram topic per profile. Only developer/researcher topics were verified on the original environment; that fact does not transfer.
- The gateway and updater default to the floating `nousresearch/hermes-agent:latest` image. `./deploy.sh hermes up` pulls it before starting; `./deploy.sh hermes update` pulls it before its transactional canary/rollback. This gives new installations the newest published image but cannot guarantee identical behavior across environments or dates. To opt into reproducibility instead, set `HERMES_IMAGE` to a tested version tag and verify the pulled digest and CLI checks above. The team templates were validated against a local Hermes v0.23.3 CLI; Docker startup itself was not available in the development environment. Hermes' config validator may warn about `agent.reasoning_effort` despite the tested runtime reading it.
- This repo deliberately excludes credentials, runtime state, sessions, Kanban SQLite databases, personal memory, demo application code and local project worktrees. Migrating those requires a separate secure backup/restore, not Git. Network, Docker daemon and external provider credentials are needed for a real cross-host gateway test.
