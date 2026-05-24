# Seluj

**Autonomous AI Software Engineer** — the most comprehensive open-source coding agent.

Seluj integrates with your repositories, understands your entire codebase, and works autonomously to fix bugs, write tests, build features, and refactor code. You describe what needs to be done, Seluj handles the rest — exploring, planning, coding, testing, and submitting a PR.

## Quick Start

```bash
# Install
git clone https://github.com/New1Direction/Seluj.git
cd Seluj && pip install -r requirements.txt

# Set your API key
export SELUJ_API_KEY="sk-your-key-here"

# Run a task
./seluj.sh "Add unit tests for the authentication module"
```

## Why Seluj?

| Capability | Seluj | Other tools |
|-----------|-------|-------------|
| **Autonomous execution** | Full async agent | Suggestions or interactive only |
| **Plan-first workflow** | Required — explore → plan → approve → execute | Rare |
| **Tool surface** | 33 tools | 3-12 typically |
| **Sandbox execution** | Isolated environment | None |
| **Frontend verification** | Playwright-based | None |
| **Model agnostic** | Any LLM provider | Locked to one vendor |
| **Open source** | MIT license | Proprietary |

## How It Works

```
User Prompt → EXPLORE (read AGENTS.md, README, codebase)
            → PLAN (markdown, numbered steps)
            → APPROVE (review before any code changes)
            → EXECUTE (one step at a time, verify after each)
            → VERIFY (run tests, linters, type checks)
            → SUBMIT (branch, commit, PR ready)
```

Seluj never writes code without a plan, never marks a step complete without verification, and never modifies build artifacts.

## 33 Tools

| Category | Tools | Purpose |
|----------|-------|---------|
| **File Operations** | 8 | Read, write, search/replace, delete, rename, restore, reset |
| **Planning** | 3 | Set plan, mark complete, record approval |
| **Execution** | 5 | Bash, web search, website fetch, images |
| **User Interaction** | 2 | Message user, request input |
| **Code Review** | 3 | Request review, read/reply to PR comments |
| **Frontend** | 3 | Playwright verification, live preview |
| **Delivery** | 2 | Pre-commit checks, submit |
| **Memory** | 1 | Cross-session recording |
| **Subagents** | 2 | Delegate tasks, completion signals |

## Documentation

- [Getting Started](/docs/getting-started)
- [Running Tasks](/docs/running-tasks)
- [Environment Setup](/docs/environment)
- [Reviewing Plans & Feedback](/docs/review-plan)
- [CLI Reference](/docs/cli-reference)
- [Tools Reference](/docs/tools-reference)
- [Seluj vs The Rest](/docs/comparison)

## License

MIT — use it, modify it, ship it. Own your workflow.