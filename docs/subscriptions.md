# Using your subscriptions instead of API keys

Free OpenRouter models are fine for chat, but weak for agentic **coding** (tool-calling quality, long context, patch discipline). If you already pay for **ChatGPT Plus/Pro** or **Claude Pro/Max**, both platforms can use those subscriptions directly — no per-token API billing. Everything below is officially supported (verified against docs, Aug 2026).

**Recommended pattern:** subscription model as `primary`, free models as fallbacks.

---

## OpenClaw

### ChatGPT Plus/Pro (native OAuth — easiest)

OpenClaw has first-class "Sign in with ChatGPT" (PKCE OAuth, provider id `openai`), headless-friendly: the wizard prints an auth URL, you open it on your laptop, then paste the redirect URL/code back.

```bash
./deploy.sh openclaw cli models auth login --provider openai
```

Then edit config (`./deploy.sh openclaw config`):

```json5
model: {
  primary: "openai/gpt-5.3-codex-spark",   // OAuth-only codex model
  fallbacks: [ "openrouter/openrouter/free" ],
}
```

OpenClaw runs `openai/*` subscription routes through the official **Codex harness** (app-server) — you get real Codex coding behavior inside your assistant. Chat controls: `/codex bind`, `/codex threads`.

### Claude Pro/Max (setup-token — best for VPS)

No browser OAuth for Anthropic; two official paths. The **setup-token** path needs nothing installed in the container:

```bash
# 1. On your LAPTOP (where Claude Code is installed and logged in):
claude setup-token          # prints a long-lived sk-ant-oat01-... token

# 2. On the VPS — paste the token when prompted:
./deploy.sh openclaw cli models auth login --provider anthropic --method setup-token
```

Then set `primary: "anthropic/claude-opus-5"` (keep free fallbacks).

Alternative (heavier): install Claude Code *inside* the container and use `--method cli` — requires persisting the full container home; docs: [Claude CLI backend in Docker](https://docs.openclaw.ai/install/docker#claude-cli-backend-in-docker).

> ⚠️ OpenClaw's docs note: subscription usage draws from your plan limits, and for always-on gateways an API key is "the most predictable choice". Anthropic can change `claude -p` billing behavior; OpenClaw treats Claude CLI reuse as sanctioned per Anthropic staff, but policies move.

Auth tokens persist in `data/openclaw/agents/…` + encryption key in `data/openclaw-secret/` (both git-ignored, both mounted by compose — don't delete them).

---

## Hermes Agent

### ChatGPT Plus/Pro (device-code — fully headless, zero tunnels)

Hermes runs its own **device-code** flow: it prints a code, you open `https://auth.openai.com/codex/device` on any browser/phone, enter the code — done. No localhost callback, no SSH tricks.

```bash
./deploy.sh hermes cli auth add openai-codex
```

Then switch (`./deploy.sh hermes config`): `model.provider: openai-codex`, and pick the model with `./deploy.sh hermes cli model`. Tokens live in `data/hermes/auth.json`. If you already use Codex CLI locally, Hermes can optionally import `~/.codex/auth.json` — but a separate login is recommended.

Opt-in power mode: `model.openai_runtime: codex_app_server` hands entire turns to Codex's own runtime ([docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/codex-app-server-runtime)).

### Claude subscription — ⚠️ read this first

Hermes has a native Claude OAuth (paste-the-code, headless-safe):

```bash
./deploy.sh hermes cli auth add anthropic --type oauth
```

**But per official docs it only works on Claude Max with purchased extra-usage credits** — usage bills against the *extra* credits, never the base allowance. **Claude Pro cannot use this path** ([providers doc](https://hermes-agent.nousresearch.com/docs/integrations/providers#anthropic-native)). If you're on Pro: use ChatGPT for Hermes's main model, or an Anthropic API key.

### Bonus: let Hermes *drive* Claude Code / Codex CLI as coding tools

Independent of the main-model provider, Hermes ships bundled skills (`autonomous-ai-agents`) that delegate coding to the real CLIs via its terminal tool (`claude -p '…'`, `codex …`). The CLIs must be installed and logged in inside the container's environment — their own login, their own quota. Docs: [claude-code skill](https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-claude-code), [codex skill](https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-codex).

---

## Headless login cheat-sheet (the CLIs themselves, on a VPS)

| CLI | Headless flow |
|---|---|
| **Claude Code** | Login prints a URL → open on laptop → **paste code back**. No tunnel ever needed. Or copy `~/.claude/.credentials.json` from laptop → VPS. `claude setup-token` gives a portable `sk-ant-oat01-…` token (`CLAUDE_CODE_OAUTH_TOKEN`). |
| **Codex CLI** | Preferred: `codex login --device-auth` (enable device auth in security settings). Or tunnel the OAuth callback: `ssh -L 1455:localhost:1455 user@vps` then `codex login`. Or copy `~/.codex/auth.json` laptop → VPS (treat like a password). |

## Which subscription for which platform (TL;DR)

| You have | OpenClaw | Hermes |
|---|---|---|
| ChatGPT Plus/Pro | ✅ native OAuth, Codex harness | ✅ device-code, zero-friction |
| Claude Pro | ✅ via setup-token | ❌ (API key only) |
| Claude Max | ✅ via setup-token / CLI reuse | ⚠️ only with extra-usage credits |
