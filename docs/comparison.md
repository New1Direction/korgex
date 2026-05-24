# Korgex vs The Rest

The autonomous coding agent landscape is evolving rapidly. Here's how Korgex compares to other tools in the space.

## At a Glance

| Feature | Korgex | GitHub Copilot | Cursor AI | Cody (Sourcegraph) | Claude Code |
|---------|-------|----------------|-----------|-------------------|-------------|
| **Autonomous execution** | ✅ Full async agent | ❌ Suggestions only | ⚠️ Limited agent mode | ✅ Basic agent | ⚠️ Interactive only |
| **Plan-first mode** | ✅ `--mode plan` | ❌ | ❌ | ❌ | ⚠️ Optional |
| **Tool surface** | **~12 user-facing** | 3-5 | 8-10 | 6-8 | 10-12 |
| **Sandbox execution** | ⚠️ Docker/Modal (opt-in) | ❌ | ❌ | ❌ | ❌ |
| **Git/PR integration** | ✅ Create, comment, review | ❌ | ⚠️ Basic | ✅ Review only | ⚠️ Basic |
| **Screenshot capture** | ⚠️ Headless Chrome | ❌ | ❌ | ❌ | ❌ |
| **PR comment replies** | ✅ Get + reply | ❌ | ❌ | ✅ | ❌ |
| **Memory (cross-session)** | ❌ Not yet implemented | ❌ | ❌ | ❌ | ❌ |
| **Subagent delegation** | ✅ Agent tool | ❌ | ❌ | ❌ | ❌ |
| **MCP server support** | ✅ Runtime connect/disconnect | ❌ | ❌ | ❌ | ✅ |
| **Open source** | ✅ MIT | ❌ | ❌ | ❌ | ❌ |
| **Model agnostic** | ✅ Any OpenAI-compatible LLM | ❌ OpenAI only | ❌ Custom only | ❌ Anthropic only | ❌ Anthropic only |

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

## When to Choose Korgex

**Korgex excels at:**
- Bug fixes across multiple files
- Test generation and test suite maintenance
- Feature implementation with full test coverage
- Dependency updates and migration
- Code refactoring with verification
- Automated PR comment responses
- Async development — fire and forget

**Not yet implemented (see roadmap):**
- Cross-session memory
- Session resume (`--resume` exits 2 today)
- Self-healing test loop outside sandbox mode
- Multi-agent orchestration for large features

## Summary

Korgex is an autonomous coding agent that's open source, provider-agnostic, and extensible via MCP. Its two-layer tool architecture (12 user-facing tools routing to 49+ internal handlers) gives the LLM a clean, well-typed surface while keeping implementation flexibility in the handlers. The default model is `claude-sonnet-4-6`; `--mode plan` upgrades to Opus with extended thinking.
