# korgex

**Autonomous coding agent. Provider-agnostic. MCP-native. Plan-first.**

A terminal-native AI engineer that reads your codebase, edits files, runs commands, and ships work. Speaks both Anthropic and OpenAI tool-use protocols. Connects to any MCP server. Streams output live to your terminal. Open source, MIT-licensed, no vendor lock-in.

```bash
$ korgex "add a /healthz endpoint that returns 200 with uptime"
➤ Read(file_path=/app/routes.py)
➤ Edit(file_path=/app/routes.py, old_string=..., new_string=...)
➤ Bash(command=pytest tests/test_routes.py -q)
✓ Added GET /healthz returning {"status": "ok", "uptime_seconds": ...}
```

---

## Table of Contents

- [Install](#install)
- [Quickstart](#quickstart)
- [How it works](#how-it-works)
- [Verifiable cognition](#verifiable-cognition)
- [CLI reference](#cli-reference)
- [Tools](#tools)
- [Environment variables](#environment-variables)
- [Multi-model routing](#multi-model-routing)
- [MCP integration](#mcp-integration)
- [Streaming TUI](#streaming-tui)
- [VS Code sidecar](#vs-code-sidecar)
- [Dashboard API](#dashboard-api)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Development](#development)
- [Testing](#testing)
- [Building & releasing](#building--releasing)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [License](#license)

---

## Install

### From GitHub Release (recommended today)

```bash
pip install https://github.com/New1Direction/korgex/releases/download/v0.6.1/korgex-0.6.1-py3-none-any.whl
```

### From source

```bash
git clone https://github.com/New1Direction/korgex.git
cd korgex
pip install -e .
```

### From `git+https` (latest `main`)

```bash
pip install git+https://github.com/New1Direction/korgex.git
```

### From PyPI

Planned. Until then use one of the above.

---

## Quickstart

```bash
# 1. Set an API key — either provider works
export ANTHROPIC_API_KEY="sk-ant-..."
# or
export OPENAI_API_KEY="sk-proj-..."
# or via OpenRouter (OpenAI-compatible, many models)
export KORGEX_API_KEY="sk-or-v1-..."
export KORGEX_API_URL="https://openrouter.ai/api/v1"

# 2. Run the agent on a naked prompt
korgex "fix the failing test in tests/test_auth.py"

# 3. Or pick a specific model / mode
korgex --model claude-sonnet-4-6 "refactor src/handler.py"
korgex --mode plan "design a rate limiter for the API"
korgex --quiet "list the python files in src/"
```

---

## How it works

```
┌──────────────────────────────────────────────────────────────────┐
│                     KORGEX AGENT LOOP                             │
│                                                                  │
│  user prompt                                                     │
│     │                                                            │
│     ▼                                                            │
│  ┌─────────────────────────┐                                     │
│  │  KorgexAgent.run_task() │                                     │
│  └─────────────────────────┘                                     │
│     │                                                            │
│     ▼                                                            │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  for i in range(max_iter):                                │    │
│  │    response = LLM.send(messages, tools)                   │    │
│  │    if no tool_calls → return final text                   │    │
│  │    for call in tool_calls:                                │    │
│  │      result = route_tool_call(name, args)                 │    │
│  │      messages.append(tool_result)                         │    │
│  └──────────────────────────────────────────────────────────┘    │
│     │                                                            │
│     ▼                                                            │
│  ┌─────────────────────────────────────────┐                     │
│  │  Provider branching                      │                     │
│  │  - "claude" in model → Anthropic SDK     │                     │
│  │  - else → OpenAI SDK (works for          │                     │
│  │    OpenAI, OpenRouter, Ollama, etc.)     │                     │
│  └─────────────────────────────────────────┘                     │
│     │                                                            │
│     ▼                                                            │
│  ┌─────────────────────────────────────────┐                     │
│  │  Tool routing (src/tool_abstraction.py) │                     │
│  │  - 12 Claude-Code-style user tools      │                     │
│  │  - Adapter layer → Jules-style handlers │                     │
│  │  - MCP-sourced tools → MCP manager       │                     │
│  └─────────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────────┘
```

The agent is provider-agnostic by design: tool schemas are translated per provider (`{name, description, input_schema}` for Anthropic, `{type: "function", function: {...}}` for OpenAI), responses are normalized into a common shape, and tool results are formatted in whichever message structure the provider expects.

---

## Verifiable cognition

What sets korgex apart: every run is recorded to a **tamper-evident causal ledger**, not an opaque log. Each event is hash-linked (`prev_hash`/`entry_hash`) to the previous one, so a whole session can be cryptographically proven intact — any edit, deletion, reorder, or splice is detected and localized to the offending event.

```bash
# Prove a recorded run was not altered after the fact
korgex verify .korg/journal.jsonl
#   ✓ ledger intact — 7 events, hash-chain verified      (exit 0; exit 1 + the bad seq_id if tampered)

# Set KORG_LEDGER_HMAC_KEY to make the chain tamper-PROOF, not just tamper-evident
export KORG_LEDGER_HMAC_KEY=…
```

On top of the chain, korgex tracks **memory drift**: a remembered fact is anchored to a sha256 baseline of its source, so when the source moves on the staleness is an exact signal — and the keep/refresh/discard reconcile decision is itself recorded to the ledger.

```bash
# Scan persistent memories for drift against their recorded source baselines
korgex drift
#   ✗ memory DRIFT — 1 drifted … reconcile is recorded to the ledger    (exit 0 if none, 1 if drift)
```

See [Self-Coding Bench](docs/self-coding-bench.md) for live reliability data across five models.

---

## CLI reference

```
$ korgex --help

usage: korgex [-h] SUBCOMMAND ...

korgex — autonomous coding agent. Pass a naked prompt to run the agent,
or use a subcommand.

positional arguments:
  SUBCOMMAND
    serve            Start dashboard + open VS Code with the sidecar.
    dashboard        Start the web dashboard only.
    init             Install Python deps + compile the VS Code extension.
    status           Check if the backend is running.
    stop             Stop the running backend.
    install-extension
                     Install the .vsix into VS Code.
    verify           Prove the cognition ledger is intact (hash-chain proof).
    drift            Scan memories for drift vs their source baselines.
```

### Naked-prompt invocation (the default)

Any non-subcommand argument is treated as a prompt:

```bash
korgex "create a hello.txt with the text 'hi'"
korgex --mode plan "redesign the data model"
korgex --model gpt-4o "write the test for this fix"
korgex --quiet "list all functions called from main()"
```

### Flags

| Flag | Purpose |
|---|---|
| `--model MODEL` | Override the model (e.g. `claude-sonnet-4-6`, `gpt-4o`, `openai/gpt-4o-mini`). Always wins over `--mode`. |
| `--mode {plan,execute,explore,review,debug,research}` | Mode-based model selection (see [Multi-model routing](#multi-model-routing)). |
| `--mcp` | Load MCP servers from `mcp.json` at startup. |
| `--quiet` / `-q` | Disable the streaming TUI. Only the final result text prints. Use this in pipes, scripts, CI. |
| `--resume` | Not yet implemented — exits with code 2 so scripts don't silently lose state. |

### Subcommands

| Subcommand | Behavior |
|---|---|
| `korgex serve` | Starts the FastAPI dashboard on `localhost:8090` and opens VS Code with the sidecar. |
| `korgex dashboard` | Starts the dashboard only (no editor). |
| `korgex init` | One-shot setup: pip-installs deps, npm-installs + compiles the VS Code extension. |
| `korgex status` | Reports whether the background backend is running. |
| `korgex stop` | Terminates the background backend (SIGTERM, then SIGKILL if needed). |
| `korgex install-extension` | Installs the compiled `.vsix` into your local VS Code. |
| `korgex verify [journal]` | Verify the ledger's hash-chain is intact — proves the recorded run wasn't edited, deleted, reordered, or spliced (exit 0/1, CI-friendly). |
| `korgex drift` | Scan persistent memories for drift against their recorded source baselines (exit 0/1). |

---

## Tools

The agent sees ~12 high-level tools (Claude-Code style), each with a deep description that includes usage guidance, edge cases, and anti-patterns:

| Tool | Purpose |
|---|---|
| **Read** | Read a file from disk, optionally with line offset/limit. |
| **Write** | Create a new file or overwrite an existing one with full content. |
| **Edit** | Surgical string replacement in an existing file. Auto-converted to SEARCH/REPLACE block internally. |
| **Bash** | Execute a shell command with timeout. |
| **Grep** | Search file contents by regex (uses ripgrep where available). |
| **Glob** | List files matching a pattern. |
| **Agent** | Delegate a sub-task to a specialized sub-agent. |
| **AskUserQuestion** | Ask the user a clarifying question with optional multiple-choice. |
| **TaskCreate** | Track multi-step work via a task list. |
| **Skill** | Invoke an installed skill by name. |
| **ToolSearch** | Discover tools at runtime by keyword. |

Under the hood these route to 49+ internal handlers (file ops, git, GitHub API, sandbox execution, web fetch, dependency analysis, profiler, etc.) — see `src/tools_impl.py`.

---

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Used when the model name contains "claude" or starts with "anthropic/". | — |
| `OPENAI_API_KEY` | Used for any non-Anthropic model. | — |
| `KORGEX_API_KEY` | Generic fallback if a provider-specific key isn't set. Useful for OpenRouter. | — |
| `KORGEX_API_URL` | Base URL for OpenAI-compatible endpoints (set for OpenRouter, Ollama, etc.). | `https://api.openai.com/v1` |
| `KORGEX_MODEL` | Default model when neither `--model` nor `--mode` is given. | `claude-sonnet-4-6` |
| `KORGEX_MAX_ITERATIONS` | Maximum agent loop iterations before giving up. | `30` |
| `KORGEX_MCP` | Set to `1` to auto-load MCP servers from `mcp.json` (equivalent to `--mcp`). | unset |
| `KORGEX_SANDBOX` | `modal` \| `docker` \| `direct` \| `auto`. Controls bash sandbox isolation. | `auto` |
| `KORGEX_PROVIDER` | Force the transport (`openai` \| `anthropic`), overriding model-id autodetect — e.g. drive `anthropic/*` or `google/*` models through OpenRouter. | autodetect |
| `KORG_JOURNAL_PATH` | Path to the durable JSONL ledger journal; content-addressed blobs are written beside it. | `.korg/journal.jsonl` |
| `KORG_LEDGER_HMAC_KEY` | If set, the ledger hash-chain is HMAC-keyed — tamper-*proof*, not just tamper-evident. | unset |

Provider-detection rule: if the model id contains `"claude"` or starts with `"anthropic/"`, the agent uses the Anthropic SDK. Otherwise it uses the OpenAI SDK (which works against OpenAI, OpenRouter, Ollama, DeepSeek, vLLM, and anything else that speaks OpenAI's chat-completions protocol). Set `KORGEX_PROVIDER=openai` to force the OpenAI-compatible transport even for a `claude`/`anthropic/` model id — e.g. to drive Claude through OpenRouter.

---

## Multi-model routing

`--mode` picks a model appropriate for the work type:

| Mode | Model | Generation params |
|---|---|---|
| `plan` | Opus 4.7 | `max_tokens=64000`, `thinking={budget_tokens: 20000}`, `temperature=0.7` |
| `execute` | Sonnet 4.6 | `max_tokens=64000`, `temperature=0.3` |
| `explore` | Opus 4.7 | `max_tokens=32000`, `temperature=0.5` |
| `review` | Sonnet 4.6 | `max_tokens=16000`, `temperature=0.3` |
| `debug` | Haiku 4.5 | `max_tokens=16000`, `temperature=0.2` |
| `research` | Opus 4.7 | `max_tokens=32000`, `temperature=0.7` |

Explicit `--model` always wins over `--mode`. Default (neither set) is Sonnet 4.6.

```bash
korgex --mode plan "architect a multi-tenant billing system"
korgex --mode debug "trace why this 500 is happening"
korgex --mode execute "implement the plan we just made"
```

---

## MCP integration

korgex includes a native MCP (Model Context Protocol) client. Any MCP server in your `mcp.json` becomes part of the agent's tool surface.

### As an MCP server (verify / audit / import from any host)

korgex also *is* an MCP server — `korgex mcp-server` exposes the verifiable-cognition substrate over JSON-RPC/stdio so any MCP host (Claude Desktop, Cursor, …) can call:

- **`korg_verify`** — prove a korg-ledger journal is tamper-evident-intact;
- **`korg_audit`** — audit the host agent's own Claude Code logs (import + verify), zero-config;
- **`korg_import`** — import a vendor session transcript into a verifiable chained ledger.

Wire it into your host's MCP config:

```json
{ "mcpServers": { "korg-ledger": { "command": "korgex", "args": ["mcp-server"] } } }
```

### Configure

Place an `mcp.json` in your repo root (matches the VS Code convention):

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "ghp_..."
      }
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

### Use

```bash
korgex --mcp "create a GitHub issue summarizing today's bug"
```

The agent discovers each server's tools at startup, registers them into the user-facing tool list, and routes calls back to the originating server. Server failures are logged and skipped — they never crash the agent.

---

## Streaming TUI

When stdout is a TTY, the agent streams output live via [Rich](https://rich.readthedocs.io/):

- **Thinking blocks** render in dimmed italic gray (Anthropic only)
- **Text** streams character-by-character
- **Tool calls** show a transient spinner: `⠋ Read(file_path=src/foo.py)`
- **Diffs** for Edit/Write on critical files prompt `[y/N]` confirmation
- **Ctrl+C** sends a graceful interrupt; double Ctrl+C force-kills

Streaming auto-disables when stdout is piped (e.g. `korgex "..." | tee log`), in CI, or with `--quiet`.

OpenAI/OpenRouter streaming works just like Anthropic: text deltas pipe through the same renderer, tool-call deltas are accumulated across chunks into a complete tool call.

---

## VS Code sidecar

`korgex-vscode/` contains a TypeScript extension that adds four commands (Cmd+Shift+P → "korgex"):

| Command | Action |
|---|---|
| `korgex: Refactor Current File` | POSTs to `/api/swarm/refactor` |
| `korgex: Run TDD Healer on Current File` | POSTs to `/api/swarm/heal` |
| `korgex: Profile Test Suite` | POSTs to `/api/swarm/profile` |
| `korgex: Open the Swarm Dashboard` | Opens `http://localhost:8090/dashboard` |

To install:

```bash
korgex init                # compiles the .vsix
korgex install-extension   # installs it into your local VS Code
```

The extension connects to `http://localhost:8090` by default, which matches the dashboard port. Adjust via the VS Code setting `korgex.backendUrl` if you change the port.

---

## Dashboard API

`korgex serve` (or `korgex dashboard`) starts a FastAPI server on `:8090` with these endpoints:

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | HTML dashboard |
| `/health` | GET | `{status: "ok"}` for liveness checks |
| `/api/state` | GET | Current dashboard state (current task, plan, logs) |
| `/api/new-task` | POST `{description}` | Start a new agent task in a background thread |
| `/api/approve-plan` | POST | Approve a pending plan |
| `/api/send-feedback` | POST `{feedback}` | Send mid-task feedback to the agent |
| `/api/swarm/refactor` | POST `{filepath}` | Spin a one-shot agent that refactors the given file |
| `/api/swarm/heal` | POST `{filepath, command}` | Spin a one-shot agent that fixes failing tests |
| `/api/swarm/profile` | POST `{command}` | Run `cProfile` via `PerformanceProfiler` and return top-N slowest functions |
| `/ws/logs` | WebSocket | Live log stream |

All swarm endpoints are synchronous (FastAPI thread-pools them) and return JSON: `{success, output, ...}` or `{success: false, error}` if a key is missing or the agent crashes.

---

## Architecture

### Tool routing — Claude-Code-style → Jules-style

```
User tool call (LLM-visible):     Internal handler (in src/tools_impl.py):
─────────────────────────────     ────────────────────────────────────────
Read(file_path=...)         →     tool_read_file(filepath=..., context=...)
Write(file_path=..., ...)   →     tool_write_file(filepath=..., ...)
Edit(file_path, old, new)   →     tool_replace_with_git_merge_diff(
                                    filepath=...,
                                    merge_diff="<<<<<<< SEARCH\n...")
Bash(command=...)           →     tool_run_in_bash_session(command=...)
```

The router (`src/tool_abstraction.py`):

- Looks up the user-facing tool name in `_TOOL_ROUTING`
- Applies a `param_map` (rename kwargs like `file_path → filepath`)
- Or applies a custom `adapter` for structural transforms (Edit → SEARCH/REPLACE)
- Filters out kwargs the handler doesn't accept (so schema fields like `Read.offset` don't crash handlers that haven't grown them yet)
- Auto-injects `context={'repo_root': cwd}`
- Catches exceptions and returns `{"error": ...}` so a single tool failure never kills the agent loop

MCP-sourced tools bypass `_TOOL_ROUTING` and dispatch through `MCPServerManager.call_tool()` instead.

### Provider branching

```
KorgexAgent(model="claude-sonnet-4-6")  →  provider="anthropic"
KorgexAgent(model="anthropic/claude-...")→  provider="anthropic"  (OpenRouter)
KorgexAgent(model="gpt-4o")             →  provider="openai"
KorgexAgent(model="openai/gpt-4o-mini") →  provider="openai"      (OpenRouter)
KorgexAgent(model="llama3:8b")          →  provider="openai"      (Ollama)
```

Each provider gets:
- Its own tool-schema shape
- Its own request method (`messages.create` vs `chat.completions.create`)
- Its own streaming chunk parser
- Its own assistant/tool-result message format

### Plan-first system prompt

The default system prompt directs the agent to plan, verify, diagnose-before-changing, and never modify build artifacts. See `SYSTEM_PROMPT` in `src/agent.py`.

---

## Project structure

```
korgex/
├── src/
│   ├── agent.py              # KorgexAgent class — main loop, provider branching, streaming
│   ├── cli.py                # argparse dispatch (naked-prompt + subcommands)
│   ├── tool_abstraction.py   # USER_TOOLS registry + router + MCP integration
│   ├── tools_impl.py         # ~49 internal handlers (tool_read_file, tool_bash, ...)
│   ├── tool_base.py          # Legacy Jules-style tool registry (still in use)
│   ├── interactive.py        # Streaming TUI: Rich-based renderer, spinner, interrupt handler
│   ├── model_router.py       # Mode → model mapping (plan/execute/debug/...)
│   ├── mcp_client.py         # Native MCP client (stdio JSON-RPC 2.0)
│   ├── dashboard.py          # FastAPI dashboard + /api/swarm/* endpoints
│   ├── sandbox.py            # Docker / Modal / direct subprocess sandbox
│   ├── swarm.py              # Multi-agent swarm orchestration
│   ├── self_healing.py       # TDD self-healing loop
│   ├── profiler.py           # cProfile-based perf profiler
│   ├── dependency_graph.py   # AST-based import/symbol graph
│   ├── context_compression.py# AST minimization for large files
│   ├── diff_engine.py        # SEARCH/REPLACE diff parser
│   ├── github_api.py         # GitHub PR / issue helpers
│   ├── memory.py             # Cross-session memory (planned)
│   ├── vision.py             # Image attachment handling
│   └── ...
├── korgex-vscode/            # VS Code sidecar extension (TypeScript)
│   ├── src/extension.ts      # 4 registered commands
│   ├── korgex-sidecar.vsix   # Compiled artifact (after `korgex init`)
│   └── package.json
├── tests/
│   └── test_bridge.py        # 27 tests covering router, providers, MCP, streaming, dashboard
├── docs/                     # CLI reference, comparison, getting-started
├── scripts/                  # Build helpers (package-vsix.sh, MCP conformance test)
├── packages/
│   └── mcp-native-client/    # Standalone reusable MCP client package
├── dist/                     # Built wheels and sdists
├── mcp.json                  # Default MCP server config
├── pyproject.toml            # Package metadata
└── requirements.txt          # Pinned runtime deps
```

---

## Development

### Setup

```bash
git clone https://github.com/New1Direction/korgex.git
cd korgex

# Create venv (uv recommended; falls back to python -m venv if you don't have uv)
uv venv .venv
source .venv/bin/activate

# Install with dev extras (pytest, twine, build, ruff)
uv pip install -e ".[dev]"

# Or with plain pip
pip install -e ".[dev]"
```

### Run the agent in editable mode

After `pip install -e .`, `korgex` is on your PATH and reflects live source edits:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
korgex "explain what src/agent.py does"
```

### Code style

The project uses [ruff](https://docs.astral.sh/ruff/):

```bash
ruff check src/
ruff format src/
```

---

## Testing

```bash
# Run the full test suite
pytest tests/ -v

# Run a specific test
pytest tests/test_bridge.py::test_write_routes_to_disk -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing
```

### What the tests cover (27 cases)

- **Router** (5): Read/Write/Edit route to handlers and produce filesystem effects; unknown tools return errors gracefully; the Edit adapter constructs valid SEARCH/REPLACE blocks; unsupported kwargs (Read.offset/limit) are filtered, not crashed.
- **Provider schemas** (4): Anthropic and OpenAI tool-schema shapes are correct; OpenRouter `anthropic/...` IDs are detected; missing API keys raise `RuntimeError` cleanly.
- **Mode routing** (5): `--mode plan` picks Opus, `--mode execute` picks Sonnet, `--mode debug` picks Haiku, explicit `--model` overrides, default falls back to Sonnet.
- **MCP** (3): MCP tools register into `USER_TOOLS` correctly; the router dispatches them to the MCP manager; full connect→discover→call→disconnect round-trip against a real stub subprocess.
- **Streaming** (5): Interactive mode auto-detects TTY; sessions are lazily constructed; OpenAI streaming accumulates text + multi-chunk tool calls into the right shape; text-only responses pass through.
- **Dashboard** (5): `/health` returns ok; swarm endpoints reject missing args with 400; swarm endpoints return clean JSON errors when no API key is set.

No live LLM calls in the test suite — everything is unit-tested.

---

## Building & releasing

### Build the wheel and sdist

```bash
rm -rf dist build
python -m build
# → dist/korgex-X.Y.Z-py3-none-any.whl
# → dist/korgex-X.Y.Z.tar.gz

# Validate PyPI metadata
python -m twine check dist/*
```

### Cut a GitHub Release

```bash
gh release create vX.Y.Z \
  dist/korgex-X.Y.Z-py3-none-any.whl \
  dist/korgex-X.Y.Z.tar.gz \
  --title "korgex X.Y.Z" \
  --notes "Release notes here"
```

### Publish to PyPI

```bash
python -m twine upload dist/*
# username: __token__
# password: pypi-... (token from https://pypi.org/manage/account/token/)
```

For the first upload of a new package, use an "Entire account" scoped token. After the package exists on PyPI, project-scoped tokens work.

---

## Troubleshooting

### `korgex: No API key found`

Set one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `KORGEX_API_KEY` (with `KORGEX_API_URL` for non-OpenAI endpoints).

### `ModuleNotFoundError: No module named 'anthropic'` (or `openai`, or `rich`)

The dependency wasn't installed. Either:

```bash
pip install -e .          # picks up everything from pyproject.toml
# or
pip install anthropic openai rich
```

### Agent loops forever on tool calls

The default `KORGEX_MAX_ITERATIONS` is 30. If the agent genuinely can't finish:

```bash
export KORGEX_MAX_ITERATIONS=10   # cap it harder
korgex --quiet "..."              # see only the final state
```

### `--mcp` takes a long time to start

The MCP client connects to each server synchronously at startup and waits up to 60s per server for the handshake. If your `mcp.json` references unreachable servers (missing `GITHUB_TOKEN`, `npx` not installed, network blocked), each one times out before being skipped. Remove unreachable entries from `mcp.json`, or reduce the per-server `timeout` value.

### Streaming TUI swallows my prompt's output

`korgex "..."` streams to stdout. If you need machine-readable output (e.g. piping to `jq`), use `--quiet`:

```bash
korgex --quiet "..." | tee transcript.txt
```

### `403 Forbidden` from `twine upload`

For a brand-new package on PyPI, you need an **"Entire account"** scoped token, not a project-scoped one. Project-scoped tokens can't create a package they don't yet own. Re-create the token at https://pypi.org/manage/account/token/ with the wider scope, upload, then narrow the token for future releases.

### VS Code extension commands do nothing

The extension POSTs to `http://localhost:8090/api/swarm/*` by default (matches the dashboard). Make sure:
1. The backend is running: `korgex serve` or `korgex dashboard`
2. The `korgex.backendUrl` setting in VS Code matches the port korgex is listening on (default `8090` on both sides)

---

## Known limitations

These exist today; PRs welcome.

- **OpenAI streaming has fewer rendered events than Anthropic.** Anthropic emits thinking blocks, content-block-start/stop, and message-delta usage events; OpenAI emits only text and tool-call chunks. The TUI renders both correctly but is richer for Anthropic.
- **`--resume` is not yet implemented.** Exits with code 2 rather than silently starting fresh, so scripts and CI that rely on it fail loudly.
- **Memory module is a stub.** `src/memory.py` exists but isn't wired into the agent loop.
- **Swarm endpoints share the agent's single-context loop.** They don't actually run sub-agents in parallel sandboxes (the `swarm.py` module supports it, but the `/api/swarm/*` endpoints don't use it yet).
- **TDD self-healing requires explicit invocation.** It's not yet triggered automatically on test failure.
- **Dashboard authentication is not implemented.** Don't expose port 8090 publicly without putting a reverse proxy with auth in front of it.
- **Dependency-graph and AST-compression tools (`src/dependency_graph.py`, `src/context_compression.py`) are not yet bridged into `USER_TOOLS`.** They're callable directly but not exposed to the agent.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Related projects

- **[korg](https://github.com/New1Direction/korg)** — deterministic cognitive runtime for AI agents (Rust). korgex v0.3.0+ records every tool call into a korg ledger via the `korg_bridge` PyO3 extension; the ledger is what korg-tui rewinds and korgchat builds on.
- **[korgchat](https://github.com/New1Direction/korgchat)** — chat product built on the same ledger; runs in the same `.korg/journal.json` as a korgex agent run, so you can interleave chat and autonomous edits.
- **[thumper](https://github.com/New1Direction/thumper)** — local execution + recovery substrate that runs under korgex; pre-warmed sandbox pools, persistent LSP, sub-second compile-error healing.
- **[Model Context Protocol](https://modelcontextprotocol.io/)** — the open MCP standard korgex implements.
