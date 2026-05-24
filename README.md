<div align="center">

# 🧠 Seluj

**Autonomous AI Software Engineer** — 33 tools · Plan-first · Async PR pipeline · Model agnostic · Open source

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](requirements.txt)
![Status](https://img.shields.io/badge/status-active-brightgreen)

---

</div>

Seluj integrates with your repositories, understands your entire codebase, and works autonomously to fix bugs, write tests, build features, and refactor code. You describe what needs to be done — Seluj handles the rest.

```bash
pip install -r requirements.txt
export SELUJ_API_KEY="sk-..."
./seluj.sh "Add unit tests for the authentication module"
```

---

## 📋 Table of Contents

- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Capabilities](#-capabilities)
- [33 Tools](#-33-tools)
- [Demo Ideas](#-demo-ideas)
- [Git Workflow](#-git-workflow)
- [Why Seluj?](#-why-seluj)
- [Documentation](#-documentation)
- [License](#-license)

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SELUJ AGENT LOOP                            │
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────────┐ │
│  │  USER     │   │  SELUJ   │   │  PLAN    │   │   APPROVAL       │ │
│  │  PROMPT   │──▶│ EXPLORES │──▶│  SET     │──▶│   RECORDED       │ │
│  │           │   │ codebase │   │ markdown │   │                  │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────────────┘ │
│                                                       │             │
│  ┌────────────────────────────────────────────────────┘             │
│  ▼                                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────────┐ │
│  │ EXECUTE  │   │ VERIFY   │   │ PRE-     │   │   SUBMIT         │ │
│  │ step 1..N│──▶│ read_file│──▶│ COMMIT   │──▶│   branch + commit │ │
│  │ tools    │   │ run tests│   │ checks   │   │   PR ready        │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────────────┘ │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              CAN RUN ASYNC (fire & forget)                   │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │   │
│  │  │ Agent A  │  │ Agent B  │  │ Agent C  │  │  Notify user │ │   │
│  │  │ (subtask)│  │ (subtask)│  │ (subtask)│  │  on complete │ │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────┘ │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

**Never writes code without a plan. Never marks complete without verification. Never modifies build artifacts.**

---

## ⚡ Quick Start

```bash
# 1. Clone
git clone https://github.com/New1Direction/Seluj.git
cd Seluj
pip install -r requirements.txt

# 2. Set API key (any LLM provider)
export SELUJ_API_KEY="sk-your-key-here"
export SELUJ_MODEL="gpt-4o"          # or claude, deepseek, llama, etc.

# 3. Initialize in your project
cd /path/to/your/project
/path/to/Seluj/seluj.sh --init

# 4. Run a task
/path/to/Seluj/seluj.sh "Add tests for the user authentication flow"
```

---

## 🎯 Capabilities

| Category | What Seluj Can Do | Example Prompt |
|----------|------------------|----------------|
| 🐛 **Bug Fixing** | Diagnose errors, find root cause, apply fix across files | `Fix the 500 error on the checkout page` |
| 🧪 **Test Writing** | Generate unit tests, integration tests, edge cases | `Add pytest tests for the payment processor` |
| ✨ **Feature Dev** | Build new features from description | `Add a dark mode toggle to settings` |
| 🔧 **Refactoring** | Restructure code, improve patterns | `Convert the API layer to use async/await` |
| 📦 **Deps** | Update dependencies, migrate between versions | `Bump next.js to v15 and migrate to app directory` |
| 📝 **Documentation** | Add JSDoc, docstrings, README updates | `Document the useCache hook with JSDoc` |
| 🔍 **Code Review** | Review PRs, reply to comments | `Review the open PR and address feedback` |
| 🎨 **Frontend** | Playwright verification, screenshots | `Add Playwright tests for the login form` |
| 🧹 **Cleanup** | Remove dead code, fix lint, optimize | `Remove all console.log statements` |

---

## 🛠 33 Tools

```
┌────────────────────────────────────────────────────────────────┐
│                     33 TOOLS · 9 CATEGORIES                     │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  📁 FILE OPERATIONS (8)                                        │
│  ┌──────┬──────┬────────┬──────┬──────┬──────┬───────┬──────┐ │
│  │ list │ read │ write │ merge│delete│rename│restore│reset │ │
│  └──────┴──────┴────────┴──────┴──────┴──────┴───────┴──────┘ │
│                                                                │
│  📋 PLANNING (3)            🖥 EXECUTION (5)                   │
│  ┌──────┬──────┬──────┐    ┌──────┬──────┬──────┬──────┬────┐ │
│  │ set  │ step │approv│    │ bash │search│ fetch│ image│media│ │
│  └──────┴──────┴──────┘    └──────┴──────┴──────┴──────┴────┘ │
│                                                                │
│  💬 USER (2)              🔍 CODE REVIEW (3)                   │
│  ┌──────┬──────┐          ┌──────┬──────┬──────┐               │
│  │message│input│          │request│ read│reply│               │
│  └──────┴──────┘          └──────┴──────┴──────┘               │
│                                                                │
│  🎨 FRONTEND (3)          📦 DELIVERY (2)                      │
│  ┌──────┬──────┬──────┐  ┌──────┬──────┐                       │
│  │instr │verify│preview│  │pre-  │submit│                       │
│  └──────┴──────┴──────┘  │commit│      │                       │
│                           └──────┴──────┘                       │
│  🧠 MEMORY (1)           🤖 SUBAGENTS (2)                      │
│  ┌──────────┐            ┌──────────┬──────────┐               │
│  │  record  │            │  agent   │   done   │               │
│  └──────────┘            └──────────┴──────────┘               │
└────────────────────────────────────────────────────────────────┘
```

| Category | Tools | Parameters |
|----------|-------|------------|
| 📁 **File Operations** | 8 | `list_files`, `read_file`, `write_file`, `replace_with_git_merge_diff`, `delete_file`, `rename_file`, `restore_file`, `reset_all` |
| 📋 **Planning** | 3 | `set_plan`, `plan_step_complete`, `record_user_approval_for_plan` |
| 🖥 **Execution** | 5 | `run_in_bash_session`, `google_search`, `view_text_website`, `view_image`, `read_image_file`, `read_media_file` |
| 💬 **User** | 2 | `message_user`, `request_user_input` |
| 🔍 **Code Review** | 3 | `request_code_review`, `read_pr_comments`, `reply_to_pr_comments` |
| 🎨 **Frontend** | 3 | `frontend_verification_instructions`, `frontend_verification_complete`, `start_live_preview_instructions` |
| 📦 **Delivery** | 2 | `pre_commit_instructions`, `submit` |
| 🧠 **Memory** | 1 | `initiate_memory_recording` |
| 🤖 **Subagents** | 2 | `call_hello_world_agent`, `done` |

---

## 🎬 Demo Ideas

Try these out of the box. Each demo showcases a different Seluj capability.

### Demo 1: Bug Fix (5 minutes)
```bash
git clone https://github.com/your-test-repo.git
cd your-test-repo
seluj.sh "Fix the login redirect bug — users are redirected to /home instead of /dashboard after login"
```
**What you'll see:** Seluj reads the auth flow, finds the redirect logic, patches it, runs tests, submits.

### Demo 2: Feature Addition (10 minutes)
```bash
seluj.sh "Add a /healthz endpoint that returns JSON {status: 'ok'} with a 200 status code"
```
**What you'll see:** Seluj explores the project structure, picks the right framework file, writes the endpoint, adds a test, verifies it passes.

### Demo 3: Test Generation (5 minutes)
```bash
seluj.sh "Write pytest tests for the stripe payment module — cover success, failure, and timeout cases"
```
**What you'll see:** Seluj reads the module, identifies all code paths, generates test cases, runs them, fixes any failures.

### Demo 4: Dependency Migration (15 minutes)
```bash
seluj.sh "Upgrade all outdated npm packages to their latest versions and fix any breaking changes"
```
**What you'll see:** Seluj reads package.json, detects outdated deps, updates versions, handles breaking changes across files, runs the build.

### Demo 5: Full Project Scaffold (10 minutes)
```bash
mkdir my-new-api && cd my-new-api
git init
seluj.sh "Create a FastAPI project with user authentication, a health endpoint, and pytest test suite"
```
**What you'll see:** Seluj builds a complete project from scratch — directory structure, config files, source code, tests, README.

### Demo 6: Code Review Pipeline (10 minutes)
```bash
# Create a PR with some issues, then:
seluj.sh "Review the open PR, check for security issues, and reply to any pending comments"
```
**What you'll see:** Seluj reads PR comments, analyzes the diff, replies to feedback, pushes fixes.

### Demo 7: Async Multi-Agent (15 minutes)
```bash
# Fire off multiple tasks in parallel
seluj.sh "Write tests for the auth module" --async &
seluj.sh "Add input validation to the API layer" --async &
seluj.sh "Update the README with API documentation" --async &
```
**What you'll see:** Three Seluj agents work in parallel, each in their own sandbox. Results appear as they complete.

---

## 🔄 Git Workflow

Seluj follows a structured git workflow inspired by industry best practices:

```
main ──▶ feature/user-auth ──▶ commit ──▶ push ──▶ PR ready
         ▲                    ▲          ▲
         │                    │          │
    Seluj creates       Seluj commits   User approves
    branch from main    with meaningful  push to remote
                        message
```

### Branch Naming
```
fix/description        — Bug fixes
feat/description       — New features
test/description       — Test additions
refactor/description   — Code restructuring
docs/description       — Documentation
chore/description      — Maintenance
```

### Commit Messages
Seluj generates descriptive commit messages:

```
✅ Good: "fix: correct redirect URL after login — was pointing to /home, now points to /dashboard"
✅ Good: "feat: add /healthz endpoint with JSON status response and test coverage"
❌ Bad:  "fix stuff"
❌ Bad:  "update"
```

### Pre-Commit Checks
Before every commit, Seluj runs:
1. ✅ Test suite
2. ✅ Linter
3. ✅ Type checker
4. ✅ No debug artifacts
5. ✅ Diff review

---

## 📊 Why Seluj?

| Capability | Seluj | GitHub Copilot | Cursor AI | Cody | Claude Code |
|-----------|-------|---------------|-----------|------|-------------|
| **Autonomous execution** | ✅ Full async agent | ❌ Suggestions only | ⚠️ Limited | ✅ Basic | ⚠️ Interactive |
| **Plan-first workflow** | ✅ Required | ❌ | ❌ | ❌ | ⚠️ Optional |
| **Tool surface** | **33 tools** | 3-5 | 8-10 | 6-8 | 10-12 |
| **Frontend verification** | ✅ Playwright | ❌ | ❌ | ❌ | ❌ |
| **Code review** | ✅ Full pipeline | ❌ | ❌ | ✅ Basic | ❌ |
| **Pre-commit checks** | ✅ Built-in | ❌ | ❌ | ❌ | ❌ |
| **Memory (cross-session)** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Subagent delegation** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Model agnostic** | ✅ Any LLM | ❌ OpenAI | ❌ Custom | ❌ Anthropic | ❌ Anthropic |
| **Open source** | ✅ MIT | ❌ | ❌ | ❌ | ❌ |

---

## 📚 Documentation

| Doc | Description |
|-----|-------------|
| [Getting Started](docs/getting-started.md) | Setup, first task, authentication |
| [Running Tasks](docs/running-tasks.md) | Writing prompts, monitoring, feedback |
| [Environment Setup](docs/environment.md) | Sandbox, preinstalled tools, setup scripts |
| [Reviewing Plans & Feedback](docs/review-plan.md) | Plan approval, mid-task steering |
| [CLI Reference](docs/cli-reference.md) | Commands, flags, env variables |
| [Tools Reference](docs/tools-reference.md) | All 33 tools with parameters |
| [Seluj vs The Rest](docs/comparison.md) | Competitive comparison |

---

## 📄 License

MIT — Own your workflow.