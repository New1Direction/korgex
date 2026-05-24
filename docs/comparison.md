# Korgex vs The Rest

The autonomous coding agent landscape is evolving rapidly. Here's how Korgex compares to other tools in the space.

## At a Glance

Compared against the agents korgex actually overlaps with — closed first-party (Claude Code) and the mature open-source CLI agents (Aider, OpenCode). IDE assistants (Copilot, Cursor, Windsurf) are a different category and not included.

| Feature | korgex | Claude Code | Aider | OpenCode |
|---|---|---|---|---|
| **License** | MIT | proprietary | Apache 2.0 | MIT |
| **Providers** | Anthropic + any OpenAI-compat | Anthropic only | 100+ via litellm | any |
| **Autonomous execution** | ✅ agent loop | ✅ | ⚠️ diff-approve default; `--yes-always` for autonomy | ✅ |
| **Plan-first mode** | ✅ `--mode plan` | ✅ plan mode | ⚠️ `/architect` mode | ✅ plan/build |
| **MCP server support** | ✅ runtime connect/disconnect | ✅ client + server | ⚠️ partial | ✅ native |
| **Sandbox execution** | Docker / Modal (opt-in) | seatbelt / landlock | ❌ | ⚠️ optional |
| **Git / PR integration** | ✅ create, comment, review | ✅ | ✅✅ auto-commits per change | ✅ |
| **Cross-session memory** | ❌ (roadmap) | ✅ `CLAUDE.md` + skills | ⚠️ repo-map only | ✅ `AGENTS.md` |
| **Session resume** | ❌ `--resume` exits 2 | ✅ `/resume` | ✅ `--restore-chat-history` | ✅ |
| **Subagent delegation** | ✅ Agent tool | ✅ | ❌ (architect/editor split) | ✅ |
| **Hooks / plugin ecosystem** | ❌ | ✅ rich | minimal | growing |
| **Mode-based model routing** | ✅ `--mode {plan,execute,debug,...}` | manual `/model` | per-mode `--model` flags | ✅ |
| **Bundled dashboard / IDE sidecar** | ✅ FastAPI + VS Code | ❌ | ❌ | ❌ |
| **Maintainers** | 1 | Anthropic team | 1 + active community | active OSS team |

## Detailed Comparison

### Autonomous Execution

Most coding assistants operate as **co-pilots** — they suggest completions while you type, or they wait for your next instruction after each action.

Korgex operates as an **autonomous engineer**. You give it a task, it explores the codebase, executes each step with verification, and reports back. You review and approve — you don't babysit.

**✅ Korgex:** `korgex "fix the 500 error in src/api/feedback.py"` → explores → edits → verifies → reports

**❌ Others:** requires you to guide every file change manually

### Plan-First Mode

Use `--mode plan` for architectural or design tasks. Korgex uses Opus with extended thinking and does read-only analysis — it won't touch files. The output is a structured plan you can review before deciding whether to proceed with `--mode execute`.

```bash
korgex --mode plan "how should we add rate limiting to the API?"
korgex --mode execute "add token-bucket rate limiting — see the plan we just made"
```

For `--mode execute` (the default), the agent explores and acts without a separate approval gate. Use `git diff` after a run to review changes before committing.

### Tool Surface

Korgex exposes **~12 user-facing tools** — named and documented in Claude Code style. These map internally to 49+ handler functions via a routing layer.

| User-facing tool | Purpose |
|-----------------|---------|
| **Read** | Read a file, optionally paginated |
| **Write** | Create or overwrite a file |
| **Edit** | Surgical string replacement (SEARCH/REPLACE internally) |
| **Bash** | Run a shell command |
| **Grep** | Regex search over file contents |
| **Glob** | Find files by name pattern |
| **Agent** | Delegate a sub-task to a specialised agent |
| **AskUserQuestion** | Clarify ambiguity before starting work |
| **TaskCreate** | Track multi-step work |
| **Skill** | Invoke an installed skill by name |
| **ToolSearch** | Discover available tools at runtime |

MCP servers registered at startup add their tools to this surface automatically.

### Model Agnostic

Korgex auto-detects the provider from the model name. Any model whose name contains "claude" or starts with "anthropic/" routes to the Anthropic SDK; everything else routes to the OpenAI-compatible SDK.

```bash
# Anthropic (default)
export ANTHROPIC_API_KEY="sk-ant-..."
export KORGEX_MODEL="claude-sonnet-4-6"

# OpenAI
export OPENAI_API_KEY="sk-proj-..."
export KORGEX_MODEL="gpt-4o"

# OpenRouter (any model on the platform)
export KORGEX_API_KEY="sk-or-v1-..."
export KORGEX_API_URL="https://openrouter.ai/api/v1"
export KORGEX_MODEL="anthropic/claude-opus-4"

# Local (Ollama)
export KORGEX_API_URL="http://localhost:11434/v1"
export KORGEX_MODEL="llama3.2:latest"
```

### MCP Server Support

Korgex can connect to any MCP (Model Context Protocol) server at startup or at runtime. Place an `mcp.json` in your repo root and pass `--mcp` to load it. The agent can also connect, disconnect, and list servers dynamically via `tool_mcp_connect` / `tool_mcp_disconnect`.

This is distinct from most agents where the tool surface is fixed — Korgex's tool surface expands with your infrastructure.

### Open Source

Korgex is fully open source under the MIT license. No paywalls, no usage caps, no vendor lock-in. You own your workflow entirely.

## Where korgex lags

Naming the gaps so you don't have to find them yourself. These are real and worth knowing before adopting korgex as a daily driver:

- **No cross-session memory.** Claude Code reads `CLAUDE.md`, OpenCode reads `AGENTS.md` — both remember project conventions across runs. Korgex starts fresh every invocation. (Listed on the roadmap; not built.)
- **No working `--resume`.** The flag exists but exits with code 2. If a long task is interrupted, you restart from scratch.
- **No hooks or plugin ecosystem.** Claude Code's hooks (pre/post tool, session start, prompt submit) and skills marketplace let you automate behavior outside the model. Korgex has neither today.
- **Python startup overhead.** ~500ms cold start vs <50ms for Rust/Go CLIs (Crush, Goose). Noise inside a multi-minute agent run; noticeable when piping `korgex --quiet "..."` in tight scripts.
- **Single maintainer.** Aider has years of community PRs; OpenCode has a team. Korgex's bus factor is 1.

These are addressable deltas. Korgex already leads on provider-agnostic routing, MCP-native tools, clean CLI+dashboard, and zero lock-in. The gaps above are where we're actively investing next.

## When to Choose Korgex

**Korgex excels at:**
- Bug fixes across multiple files
- Test generation and test suite maintenance
- Feature implementation with full test coverage
- Dependency updates and migration
- Code refactoring with verification
- Automated PR comment responses
- Async development — fire and forget

## Summary

Korgex is an autonomous coding agent that's open source, provider-agnostic, and extensible via MCP. Its two-layer tool architecture (12 user-facing tools routing to 49+ internal handlers) gives the LLM a clean, well-typed surface while keeping implementation flexibility in the handlers. The default model is `claude-sonnet-4-6`; `--mode plan` upgrades to Opus with extended thinking.
