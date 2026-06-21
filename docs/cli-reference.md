# CLI Reference

korgex is a single binary installed via `pip`. It has two modes: naked-prompt invocation (the default) and subcommands.

## Installation

```bash
# From PyPI
pip install -U korgex

# From source (editable, reflects live changes)
git clone https://github.com/New1Direction/korgex.git
cd korgex
pip install -e .
```

---

## Naked-prompt invocation (primary usage)

Any argument that isn't a known subcommand is treated as a prompt for the agent:

```bash
korgex "fix the failing test in tests/test_auth.py"
korgex "add a /healthz route to src/app.py"
korgex --mode plan "design a rate limiter for the API"
korgex --model gpt-4o "write the test for this fix"
korgex --quiet "list all functions exported from src/utils.py"
```

### Flags

| Flag | Description |
|------|-------------|
| `--model MODEL` | Model to use (e.g. `claude-sonnet-4-6`, `gpt-4o`, `anthropic/claude-opus-4-7`). Always overrides `--mode` and `KORGEX_MODEL`. |
| `--mode {plan,execute,explore,review,debug,research}` | Mode-based model selection. `plan` → Opus, `execute` → Sonnet, `debug` → Haiku. |
| `--mcp` | Load MCP servers from `mcp.json` at startup and expose their tools to the agent. |
| `--quiet` / `-q` | Disable the streaming TUI. Only the final result prints. Use in pipes, scripts, and CI. |
| `--version` / `-V` | Print the korgex version and exit. |
| `--resume` | Resume the last session: replays the prior session's prompts, replies, and tool calls from the verifiable journal back into context. List sessions with `korgex sessions`. |

---

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `korgex setup` | Connect a model provider (OpenRouter / Anthropic / OpenAI / Ollama) — saves the key + default model to `~/.korgex/config.json`. |
| `korgex skills` | List every available skill (built-in, project, and learned) with its description. |
| `korgex init` | Scaffold a starter `AGENTS.md` for the current repo (detects stack + test/build commands; never clobbers an existing one). |
| `korgex serve` | Start the FastAPI dashboard on `127.0.0.1:8090` by default and open VS Code with the sidecar extension. |
| `korgex dashboard` | Start the localhost dashboard only (no editor). Set `KORGEX_DASHBOARD_HOST` explicitly only behind an auth-terminating proxy. |
| `korgex status` | Report whether the background backend is running and its PID. |
| `korgex stop` | Send SIGTERM (then SIGKILL) to the background backend. |
| `korgex install-extension` | Install the compiled `.vsix` into your local VS Code. |
| `korgex verify [journal]` | Verify the ledger hash-chain is intact (tamper-evidence proof); exits 0 if intact, 1 if tampered (and prints the offending `seq_id`). Defaults to `$KORG_JOURNAL_PATH` or `.korg/journal.jsonl`. |
| `korgex near anchor [journal-or-receipt]` | Generate a privacy-preserving NEAR anchor payload for a ledger or signed receipt; prints a near-cli-js call example. |
| `korgex omnigraph export [journal-or-receipt]` | Export a verified run into Omnigraph-loadable JSONL plus an optional `.pg` schema. |
| `korgex omnigraph write [journal-or-receipt] --store graph.omni` | Export then load a verified run into an Omnigraph branch with `omnigraph load`. |
| `korgex demo near-omnigraph` | Print or write the full Korgex → Omnigraph → NEAR demo workflow. |
| `korgex drift` | Scan persistent memories for drift against their recorded source baselines; exits 0 if none drifted, 1 if drift is found. |

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Used when the model contains `"claude"` or starts with `"anthropic/"`. |
| `OPENAI_API_KEY` | — | Used for any non-Anthropic model. |
| `KORGEX_API_KEY` | — | Generic fallback if a provider-specific key isn't set. Useful for OpenRouter. |
| `KORGEX_API_URL` | `https://api.openai.com/v1` | Base URL for OpenAI-compatible endpoints (OpenRouter, Ollama, DeepSeek, etc.). |
| `KORGEX_MODEL` | `claude-sonnet-4-6` | Fallback model. Full precedence: `--model` → `--mode` → the configured default (`korgex setup`) → `KORGEX_MODEL` → builtin. |
| `KORGEX_MAX_ITERATIONS` | `30` | Maximum agent loop iterations before the agent gives up. |
| `KORGEX_MCP` | unset | Set to `1` to auto-load MCP servers from `mcp.json` (same effect as `--mcp`). |
| `KORGEX_SANDBOX` | `auto` | Bash sandbox isolation: `modal`, `docker`, `direct`, or `auto`. |
| `KORGEX_PROVIDER` | autodetect | Force the transport (`openai` or `anthropic`), overriding model-id autodetect. Lets `anthropic/*` and `google/*` models run through OpenRouter's OpenAI-compatible endpoint. |
| `KORG_JOURNAL_PATH` | `.korg/journal.jsonl` | Path to the durable JSONL ledger journal; content-addressed blobs are written beside it. Read by `korgex verify`. |
| `KORG_LEDGER_HMAC_KEY` | unset | If set, the ledger hash-chain is HMAC-keyed — tamper-*proof*, not just tamper-evident. |

**Provider detection:** if the model id contains `"claude"` or starts with `"anthropic/"`, korgex uses the Anthropic SDK. Otherwise it uses the OpenAI SDK, which covers OpenAI, OpenRouter, Ollama, DeepSeek, vLLM, and any OpenAI-compatible endpoint. Set `KORGEX_PROVIDER=openai` to force the OpenAI-compatible transport even for a `claude`/`anthropic/` model id (e.g. Claude or Gemini through OpenRouter).

---

## Mode → model mapping

| Mode | Model | Notes |
|------|-------|-------|
| `plan` | `claude-opus-4-7` | Extended thinking enabled, budget 20k tokens |
| `execute` | `claude-sonnet-4-6` | Fast, low temperature |
| `explore` | `claude-opus-4-7` | Broad analysis |
| `review` | `claude-sonnet-4-6` | Code review focus |
| `debug` | `claude-haiku-4-5` | Low latency, tight temperature |
| `research` | `claude-opus-4-7` | Web + reasoning |

Explicit `--model` always wins over `--mode`.

---

## Examples

```bash
# Default model (Sonnet 4.6 via Anthropic)
export ANTHROPIC_API_KEY=sk-ant-...
korgex "explain what src/agent.py does"

# OpenRouter with any model
export KORGEX_API_KEY=sk-or-v1-...
export KORGEX_API_URL=https://openrouter.ai/api/v1
korgex --model openai/gpt-4o "add pagination to the /users endpoint"

# Opus for deep planning
korgex --mode plan "redesign the authentication layer"

# Quiet mode for scripting
result=$(korgex --quiet "list all TODO comments in src/")
echo "$result"

# Load GitHub MCP server tools at runtime
korgex --mcp "create a GitHub issue summarising today's bug"

# Start the localhost dashboard and VS Code sidecar
korgex serve

# Check backend status
korgex status

# Drive Claude (or Gemini) through OpenRouter's OpenAI-compatible endpoint
export KORGEX_API_KEY=sk-or-v1-...
export KORGEX_API_URL=https://openrouter.ai/api/v1
export KORGEX_PROVIDER=openai
korgex --model anthropic/claude-sonnet-4.6 "refactor the parser"

# Prove a recorded run's ledger was not altered (exit 0 = intact, 1 = tampered)
korgex verify .korg/journal.jsonl

# Mint a signed proof, then prepare a NEAR testnet anchor payload with hashes only
korgex receipt .korg/journal.jsonl --sign --claim "fixed issue #123" --out .korg/receipts/issue-123.json
korgex near anchor .korg/receipts/issue-123.json --account you.testnet --contract korgex-anchor.testnet

# Export the same proof into an Omnigraph dev graph branch
korgex omnigraph export .korg/receipts/issue-123.json --out .korg/omnigraph/issue-123.jsonl --schema-out .korg/omnigraph/korgex-dev.pg
korgex omnigraph write .korg/receipts/issue-123.json --store devgraph.omni --branch agent/issue-123 --from main

# Generate the full demo script
korgex demo near-omnigraph --account YOU.testnet --contract korgex-anchor.YOU.testnet --write .korg/demos/near-omnigraph.sh

# Scan persistent memories for drift against their source baselines
korgex drift
```
