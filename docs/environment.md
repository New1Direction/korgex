# Environment Setup

korgex runs locally by default — it reads and writes files in your current working directory and executes bash commands in a subprocess. An optional sandbox layer (Docker or Modal) isolates execution for untrusted code or parallel workloads.

## Default (local, no sandbox)

No configuration required. korgex runs commands directly in your shell environment via `subprocess`. This is fine for most tasks:

```bash
korgex "fix the failing test in tests/test_auth.py"
```

The agent's Bash tool inherits your shell's PATH, virtualenv, and environment variables, so any tool or package you can run in your terminal the agent can too.

## Sandbox isolation

Set `KORGEX_SANDBOX` to isolate bash execution:

| Value | Behaviour |
|-------|-----------|
| `auto` (default) | Direct local execution. No isolation. |
| `direct` | Same as `auto` — explicit opt-in to local execution. |
| `docker` | Runs bash commands inside a Docker container with your repo mounted. Requires Docker. |
| `modal` | Runs bash commands in an ephemeral Modal cloud sandbox. Requires a Modal account and `pip install modal`. |

```bash
# Docker sandbox
export KORGEX_SANDBOX=docker
korgex "run the full test suite and report failures"

# Modal sandbox
export KORGEX_SANDBOX=modal
korgex "benchmark this sorting function against the stdlib"
```

Sandbox isolation affects only the **Bash tool** (`tool_run_in_bash_session`). File reads/writes and other tools always operate on your local filesystem.

## API key configuration

korgex needs a key from at least one LLM provider:

```bash
# Anthropic (used for any model containing "claude")
export ANTHROPIC_API_KEY="sk-ant-..."

# OpenAI (used for any other model)
export OPENAI_API_KEY="sk-proj-..."

# Generic fallback — useful for OpenRouter or custom endpoints
export KORGEX_API_KEY="sk-or-v1-..."
export KORGEX_API_URL="https://openrouter.ai/api/v1"
```

## Custom model endpoint

Point korgex at any OpenAI-compatible server:

```bash
# Ollama (local)
export KORGEX_API_URL="http://localhost:11434/v1"
export KORGEX_MODEL="llama3.2:latest"

# DeepSeek
export KORGEX_API_URL="https://api.deepseek.com/v1"
export KORGEX_MODEL="deepseek-coder"

# Future custom korg model on Google Cloud
export KORGEX_API_URL="https://your-korg-endpoint.googleapis.com/v1"
export KORGEX_MODEL="korg-v1"
```

## AGENTS.md

Place an `AGENTS.md` in your repo root to give korgex project-specific context: build commands, test runner, conventions, things to avoid:

```bash
korgex init   # scaffolds a starter AGENTS.md
```

The agent reads `AGENTS.md` at the start of every task, before touching any code.

## MCP servers

Place an `mcp.json` in your repo root (VS Code format) to extend the agent's tool surface with external servers:

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "ghp_..." }
    }
  }
}
```

Load at runtime with `--mcp` or set `KORGEX_MCP=1` to always load.

## All environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `KORGEX_API_KEY` | — | Generic fallback key |
| `KORGEX_API_URL` | `https://api.openai.com/v1` | Base URL for OpenAI-compatible providers |
| `KORGEX_MODEL` | `claude-sonnet-4-6` | Default model |
| `KORGEX_MAX_ITERATIONS` | `30` | Max agent loop iterations |
| `KORGEX_MCP` | unset | `1` to auto-load `mcp.json` |
| `KORGEX_SANDBOX` | `auto` | `auto`, `direct`, `docker`, or `modal` |
