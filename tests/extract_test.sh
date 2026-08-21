#!/usr/bin/env bash
# T2/T3 — chain extraction from platform configs via --print-chain.
source "$(dirname "$0")/helpers.sh"
sandbox_setup
trap sandbox_teardown EXIT
cd "$SANDBOX"

# --- OpenClaw: template fallback (no data/ seeded) — the 4 curated ids, in order
out="$(scripts/doctor.sh openclaw --print-chain)"
expected='openrouter:nvidia/nemotron-3-ultra-550b-a55b:free
openrouter:z-ai/glm-5.2:free
openrouter:nvidia/nemotron-3-super-120b-a12b:free
openrouter:google/gemma-4-31b-it:free'
assert_eq "$expected" "$out" "(openclaw template chain)"

# --- OpenClaw: seeded data/ takes precedence; gemini primary is detected;
# commented-out alternatives never leak.
mkdir -p data/openclaw
cat > data/openclaw/openclaw.json <<'EOF'
{
  agents: {
    defaults: {
      model: {
        primary: "google/gemini-3.6-flash",
        // primary: "openrouter/should-not/appear:free",
        fallbacks: [
          "openrouter/z-ai/glm-5.2:free",
        ],
      },
    },
  },
}
EOF
out="$(scripts/doctor.sh openclaw --print-chain)"
expected='gemini:gemini-3.6-flash
openrouter:z-ai/glm-5.2:free'
assert_eq "$expected" "$out" "(openclaw seeded + gemini)"

echo "   openclaw extraction ok"
