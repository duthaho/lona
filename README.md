# Lona

**Your personal AI assistant, self-hosted with one command.**

Lona is a batteries-included deployment kit for [OpenClaw](https://openclaw.ai) and [Hermes Agent](https://github.com/NousResearch/hermes-agent) — two of the leading open-source personal AI assistant platforms. Pick one, run one command on any Linux server, and talk to your assistant on Telegram minutes later.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![OpenClaw](https://img.shields.io/badge/platform-OpenClaw-orange)](https://openclaw.ai)
[![Hermes Agent](https://img.shields.io/badge/platform-Hermes%20Agent-purple)](https://github.com/NousResearch/hermes-agent)

## Highlights

- **One command, zero manual setup** — `./deploy.sh openclaw` or `./deploy.sh hermes` handles Docker installation, secret generation, config seeding, and startup
- **Free by default** — ships configured for OpenRouter's free-model router with automatic failover; runs at $0/month
- **Subscription-powered coding** — use your existing ChatGPT Plus/Pro or Claude Pro/Max plan as the assistant's brain, no API billing
- **Telegram-native** — DMs, groups, and forum topics (each topic is an isolated session); long-polling means no inbound ports and works behind NAT
- **Secure defaults** — deny-by-default access control, loopback-only dashboards, auto-generated secrets, secrets never committed
- **Portable state** — everything lives in `./data/`; back up, migrate, or wipe with a single directory

## Architecture

```mermaid
flowchart LR
    U([You]) <--> TG[Telegram]
    TG <-- long polling --> GW

    subgraph VPS [Your server · Docker]
        GW["Gateway container<br/>(OpenClaw or Hermes)"]
        DATA[("./data/<br/>config · sessions · memory")]
        GW --- DATA
    end

    GW --> OR["OpenRouter<br/>(free models)"]
    GW -.-> SUB["ChatGPT / Claude<br/>(your subscription)"]
    U -. SSH tunnel .-> UI["Dashboard<br/>127.0.0.1 only"]
    UI --- GW
```

## Quick start

### Prerequisites

| | Where to get it |
|---|---|
| A Linux server (or any machine with Docker) | any VPS provider — 1 vCPU / 1 GB is enough |
| OpenRouter API key | [openrouter.ai/keys](https://openrouter.ai/keys) — free, no credit card |
| Telegram bot token | DM [@BotFather](https://t.me/BotFather) → `/newbot` |
| Your Telegram user id | DM [@userinfobot](https://t.me/userinfobot) |

### Fresh server — one line

```bash
curl -fsSL https://raw.githubusercontent.com/duthaho/lona/main/scripts/bootstrap.sh | bash -s -- openclaw
```

Installs git and Docker if missing, clones this repo to `~/lona`, and runs the deploy. Use `hermes` instead of `openclaw` to pick the other platform.

### Machine with Docker

```bash
git clone https://github.com/duthaho/lona.git && cd lona
./deploy.sh openclaw          # or: ./deploy.sh hermes
```

First run prompts for your OpenRouter key and Telegram token, generates auth secrets, seeds the config, pulls the image, and starts the gateway. Then just DM your bot.

## Commands

```
./deploy.sh <openclaw|hermes> [action]
```

| Action | Description |
|---|---|
| `up` *(default)* | Install prerequisites, seed config, pull image, start |
| `down` / `restart` | Stop / restart |
| `logs` | Follow container logs |
| `status` | Container status |
| `config` | Edit the platform config in `$EDITOR`, apply on save |
| `update` | Pull the latest image and recreate |
| `backup` | Archive `./data/<platform>` into `./backups/` |
| `cli …` | Run the platform's own CLI inside the container |

## Models

### Free tier (default)

Both platforms ship with a **curated chain of free models**, ordered for reliability first:

1. `nvidia/nemotron-3-ultra-550b-a55b:free` — strong agentic reasoner, 1M context, hosted by NVIDIA itself so it's rarely rate-limited
2. `z-ai/glm-5.2:free` — the smartest free model by [Artificial Analysis](https://artificialanalysis.ai) index, but its single provider is frequently congested (429s), so it serves as fallback / manual switch
3. `nvidia/nemotron-3-super-120b-a12b:free` / `google/gemma-4-31b-it:free` — additional fallbacks (Gemma adds vision)

We deliberately avoid OpenRouter's `openrouter/free` auto-router: it picks free models *at random*, including tiny low-quality ones.

**Gemini for free:** OpenRouter's free pool no longer carries Gemini/DeepSeek/Grok, but Google's own [AI Studio](https://aistudio.google.com) key has a generous free tier. Set `GOOGLE_API_KEY` in `.env` and switch the model to `gemini-3.6-flash` (both platforms support it natively — see the config templates; older Gemini 2.5 models are closed to new API users).

> **Quota note:** OpenRouter's free-tier daily cap is per *account*, not per model. A one-time $10 credit purchase raises it to 1,000 requests/day — the best value upgrade for a personal assistant. Free model ids rotate; browse [openrouter.ai/models?max_price=0](https://openrouter.ai/models?max_price=0).

### Your subscription (recommended for coding)

Free models handle chat well but lag on agentic coding. If you already pay for **ChatGPT Plus/Pro** or **Claude Pro/Max**, both platforms can use those plans directly — headless-friendly login included:

```bash
./deploy.sh openclaw cli models auth login --provider openai     # ChatGPT → OpenClaw
./deploy.sh hermes cli auth add openai-codex                     # ChatGPT → Hermes
```

Full setup, the Claude paths, and platform caveats: **[docs/subscriptions.md](docs/subscriptions.md)**.

## Telegram access control

| | OpenClaw | Hermes |
|---|---|---|
| **DMs** | Pairing: stranger gets a code, you approve it | Allowlist: `TELEGRAM_ALLOWED_USERS` |
| **Groups** | Group allowlist, injected by deploy.sh from `TELEGRAM_GROUP_ALLOWED_CHATS` | Your allowlisted users work instantly; add the group id to open it to all members |
| **Group noise** | `requireMention: true` set per injected group | `require_mention: true` set in the template |
| **Forum topics** | Own session per topic; per-topic agent override | Own session per topic; per-topic skill binding |

To use the bot in a group: disable privacy mode in @BotFather (`/setprivacy`) or make the bot an admin, **remove and re-add the bot**, grab the chat id from `logs`, set `TELEGRAM_GROUP_ALLOWED_CHATS` in `.env`, and run `up`.

## Day-2 operations

| Task | How |
|---|---|
| Change model, channels, skills | `./deploy.sh <platform> config` |
| Change secrets or ports (`.env`) | edit `.env`, then `./deploy.sh <platform> up` (recreate — `restart` won't pick up env) |
| Upgrade platform version | `./deploy.sh <platform> update` |
| Back up all state | `./deploy.sh <platform> backup` |
| Switch platforms | `./deploy.sh openclaw down && ./deploy.sh hermes` — shared `.env` |
| Open the dashboard | `ssh -L 18789:127.0.0.1:18789 user@vps` (OpenClaw) / `ssh -L 9119:127.0.0.1:9119 user@vps` (Hermes) |

## Security model

- **Network:** only Telegram long-polling leaves the box; dashboards bind to loopback and are reached via SSH tunnel. Expose them only behind a TLS reverse proxy (change `OPENCLAW_BIND` / `HERMES_DASHBOARD_BIND`).
- **Access:** deny-by-default on both platforms — unknown Telegram users are blocked, groups must be allowlisted.
- **Secrets:** generated automatically, stored only in `.env` (`chmod 600`, git-ignored); OAuth tokens persist in git-ignored `./data/`.

## Repository layout

```
deploy.sh                      One-command deploy and management
docker-compose.yml             Both platforms as Compose profiles
.env.example                   Single configuration template
config/
  openclaw/openclaw.json       OpenClaw template (seeded to data/ on first run)
  hermes/config.yaml           Hermes template (seeded to data/ on first run)
scripts/bootstrap.sh           curl-able installer for a fresh server
docs/subscriptions.md          Using ChatGPT / Claude plans instead of API keys
data/                          Runtime state (git-ignored)
```

## License

[MIT](LICENSE)
