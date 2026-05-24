# Getting Started

korgex is an autonomous coding agent that fixes bugs, writes tests, builds features, and refactors code. You give it a task in plain English; it reads your codebase, makes changes, runs verification, and reports back.

## Prerequisites

- Python 3.10+
- An API key from Anthropic, OpenAI, or any OpenAI-compatible provider (OpenRouter, etc.)

## Install

```bash
pip install https://github.com/New1Direction/korgex/releases/download/v0.2.2/korgex-0.2.2-py3-none-any.whl
```

Verify:

```bash
korgex --help
```

## Set an API key

korgex auto-detects the provider from the model name.

```bash
# Anthropic (default model is claude-sonnet-4-6)
export ANTHROPIC_API_KEY="sk-ant-..."

# OpenAI
export OPENAI_API_KEY="sk-proj-..."

# OpenRouter (works with any model on the platform)
export KORGEX_API_KEY="sk-or-v1-..."
export KORGEX_API_URL="https://openrouter.ai/api/v1"
```

## Run your first task

```bash
korgex "add a /healthz endpoint that returns 200 with uptime"
```

korgex will stream its work to the terminal — file reads, edits, bash commands — then print the result when done.

## What happens under the hood

1. **Plan first** — the agent reads `README.md` and any `AGENTS.md`, explores relevant files, and forms a plan before touching anything.
2. **Execute** — reads files, makes targeted edits via SEARCH/REPLACE, runs commands to verify.
3. **Verify** — re-reads every file it changed to confirm the edit applied correctly.
4. **Report** — prints a summary and exits.

## Pick a model or mode

```bash
# Explicit model
korgex --model claude-opus-4-7 "architect the new billing system"

# Mode-based (plan → Opus, debug → Haiku, etc.)
korgex --mode plan "design a rate limiter"
korgex --mode debug "trace the 500 error in /api/login"
```

## Quiet mode (for scripts and CI)

```bash
korgex --quiet "list all TODO comments in src/" > todos.txt
```

Disables the streaming TUI and prints only the final result.

## MCP servers

If you have an `mcp.json` in your repo root (VS Code format), korgex can load those servers and expose their tools to the agent:

```bash
korgex --mcp "create a GitHub issue for the bug we just fixed"
```

## Dashboard and VS Code sidecar

```bash
korgex init     # install deps + compile the VS Code extension
korgex serve    # start dashboard on :8090 and open VS Code
```

The dashboard exposes `/api/swarm/refactor`, `/api/swarm/heal`, and `/api/swarm/profile` which the VS Code extension calls directly.

## Next steps

- [CLI Reference](/docs/cli-reference) — all flags and subcommands
- [Tools Reference](/docs/tools-reference) — the full tool surface
- [Running Tasks](/docs/running-tasks) — prompt patterns and workflow tips
- [MCP Integration](/docs/cli-reference#mcp-servers) — connecting external tool servers
