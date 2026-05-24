# KorgKode vs The Rest

The autonomous coding agent landscape is evolving rapidly. Here's how KorgKode compares to other tools in the space.

## At a Glance

| Feature | KorgKode | GitHub Copilot | Cursor AI | Cody (Sourcegraph) | Claude Code |
|---------|-------|----------------|-----------|-------------------|-------------|
| **Autonomous execution** | ✅ Full async agent | ❌ Suggestions only | ⚠️ Limited agent mode | ✅ Basic agent | ⚠️ Interactive only |
| **Plan-first workflow** | ✅ Required | ❌ | ❌ | ❌ | ⚠️ Optional |
| **Tool surface** | **33 tools** | 3-5 | 8-10 | 6-8 | 10-12 |
| **Sandbox execution** | ✅ Isolated VM | ❌ | ❌ | ❌ | ❌ |
| **Git/PR integration** | ✅ Full pipeline | ❌ | ⚠️ Basic | ✅ Review only | ⚠️ Basic |
| **Frontend verification** | ✅ Playwright | ❌ | ❌ | ❌ | ❌ |
| **Code review** | ✅ Request + reply to PR comments | ❌ | ❌ | ✅ | ❌ |
| **Pre-commit checks** | ✅ Built-in | ❌ | ❌ | ❌ | ❌ |
| **Memory (cross-session)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Subagent delegation** | ✅ Agency-style | ❌ | ❌ | ❌ | ❌ |
| **Open source** | ✅ MIT | ❌ | ❌ | ❌ | ❌ |
| **Model agnostic** | ✅ Any LLM | ❌ OpenAI only | ❌ Custom only | ❌ Anthropic only | ❌ Anthropic only |

## Detailed Comparison

### Autonomous Execution

Most coding assistants operate as **co-pilots** — they suggest completions while you type, or they wait for your next instruction after each action.

KorgKode operates as an **autonomous engineer**. You give it a task, it explores the codebase, formulates a plan, executes each step with verification, and submits the completed work. You review and approve — you don't babysit.

**✅ KorgKode:** "Add authentication middleware" → explores → plans → builds → tests → submits a PR
**❌ Others:** "Add authentication middleware" → you guide every file change manually

### Plan-First Workflow

KorgKode never writes code without a plan. Every task begins with codebase exploration followed by a structured markdown plan. You approve the plan before any code changes are made.

This means:
- No wasted work on wrong approaches
- Clear visibility into what KorgKode intends to do
- Ability to course-correct before code is written

### Tool Surface

KorgKode exposes **33 tools** — the most comprehensive tool surface of any coding agent:

| Category | Tools | Purpose |
|----------|-------|---------|
| **File Operations** | 8 tools | Read, write, search/replace, delete, rename, restore, reset |
| **Planning** | 3 tools | Set plan, mark step complete, record approval |
| **Execution** | 5 tools | Bash, web search, website fetch, image viewing |
| **User Interaction** | 2 tools | Message user, request input |
| **Code Review** | 3 tools | Request review, read comments, reply |
| **Frontend** | 3 tools | Playwright instructions, verification, live preview |
| **Delivery** | 2 tools | Pre-commit checks, submit with branch & commit |
| **Memory** | 1 tool | Cross-session recording |
| **Subagents** | 2 tools | Delegate to sub-agents, completion signal |

### Model Agnostic

KorgKode doesn't lock you into a single LLM provider. Configure any model:

```bash
# OpenAI
export KORGKODE_API_URL="https://api.openai.com/v1"
export KORGKODE_MODEL="gpt-4o"

# Anthropic
export KORGKODE_API_URL="https://api.anthropic.com/v1"
export KORGKODE_MODEL="claude-sonnet-4-20250514"

# OpenRouter
export KORGKODE_API_URL="https://openrouter.ai/api/v1"
export KORGKODE_MODEL="anthropic/claude-sonnet-4"

# Local (Ollama)
export KORGKODE_API_URL="http://localhost:11434/v1"
export KORGKODE_MODEL="llama3"
```

### Open Source

KorgKode is fully open source under the MIT license. No paywalls, no usage caps, no vendor lock-in. You own your workflow entirely.

## When to Choose KorgKode

**KorgKode excels at:**
- Bug fixes across multiple files
- Test generation and test suite maintenance
- Feature implementation with full test coverage
- Dependency updates and migration
- Code refactoring with verification
- Automated code review responses
- Async development — fire and forget

**KorgKode is evolving toward:**
- Multi-agent orchestration for large features
- CI/CD integration for automated PR responses
- Real-time collaboration with human developers

## Summary

KorgKode occupies a unique position: the most comprehensive autonomous coding agent that's fully open source and model agnostic. It combines the autonomy of an async agent with the verification rigor of a senior engineer, backed by the largest tool surface in the category.