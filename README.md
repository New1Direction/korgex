<div align="center">

# 🧠 KorgKode

**Autonomous AI Software Engineer** — 33 tools · Plan-first · Async PR pipeline · Model agnostic · Open source

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](requirements.txt)
![Status](https://img.shields.io/badge/status-active-brightgreen)

---

</div>

KorgKode integrates with your repositories, understands your entire codebase, and works autonomously to fix bugs, write tests, build features, and refactor code. You describe what needs to be done — KorgKode handles the rest.

```bash
# One-shot install
pip install korgkode

# Launch the editor
korgkode
```

**Or from source:**

```bash
git clone https://github.com/New1Direction/KorgKode.git
cd KorgKode
pip install -e .
korgkode init          # install deps + compile VS Code extension
korgkode               # launch backend + VS Code sidecar
```

---

## 📋 Table of Contents

- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Capabilities](#-capabilities)
- [33 Tools](#-33-tools)
- [Demo Ideas](#-demo-ideas)
- [Git Workflow](#-git-workflow)
- [Why KorgKode?](#-why-korgkode)
- [Enterprise: Zero-Hallucination](#-enterprise-zero-hallucination)
- [Documentation](#-documentation)
- [License](#-license)

---

## 📟 CLI & VS Code Extension

The `korgkode` CLI is the primary entry point:

| Command | What it does |
|---|---|
| `korgkode` | Starts the FastAPI backend + opens VS Code with the sidecar |
| `korgkode init` | One-shot setup: installs Python deps, compiles the extension |
| `korgkode dashboard` | Opens the web dashboard on port 8090 |
| `korgkode status` | Checks if the backend is running |
| `korgkode stop` | Stops the background backend server |
| `korgkode install-extension` | Installs the `.vsix` into VS Code |

**VS Code commands** (Cmd+Shift+P after installing the sidecar):

| Command | Action |
|---|---|
| `KorgKode: Refactor Current File` | Sends the active file to the KorgKode swarm |
| `KorgKode: Run TDD Healer on Current File` | Prompts for a test command, runs the healer |
| `KorgKode: Profile Test Suite` | Runs cProfile via the performance profiler |
| `KorgKode: Open the Swarm Dashboard` | Opens `http://localhost:8090/dashboard` in your browser |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        KORGKODE AGENT LOOP                            │
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────────────┐ │
│  │  USER     │   │  KORGKODE   │   │  PLAN    │   │   APPROVAL       │ │
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
git clone https://github.com/New1Direction/KorgKode.git
cd KorgKode
pip install -r requirements.txt

# 2. Set API key (any LLM provider)
export KORGKODE_API_KEY="sk-your-key-here"
export KORGKODE_MODEL="gpt-4o"          # or claude, deepseek, llama, etc.

# 3. Initialize in your project
cd /path/to/your/project
/path/to/KorgKode/korgkode.sh --init

# 4. Run a task
/path/to/KorgKode/korgkode.sh "Add tests for the user authentication flow"
```

---

## 🎯 Capabilities

| Category | What KorgKode Can Do | Example Prompt |
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

Try these out of the box. Each demo showcases a different KorgKode capability.

### Demo 1: Bug Fix (5 minutes)
```bash
git clone https://github.com/your-test-repo.git
cd your-test-repo
korgkode.sh "Fix the login redirect bug — users are redirected to /home instead of /dashboard after login"
```
**What you'll see:** KorgKode reads the auth flow, finds the redirect logic, patches it, runs tests, submits.

### Demo 2: Feature Addition (10 minutes)
```bash
korgkode.sh "Add a /healthz endpoint that returns JSON {status: 'ok'} with a 200 status code"
```
**What you'll see:** KorgKode explores the project structure, picks the right framework file, writes the endpoint, adds a test, verifies it passes.

### Demo 3: Test Generation (5 minutes)
```bash
korgkode.sh "Write pytest tests for the stripe payment module — cover success, failure, and timeout cases"
```
**What you'll see:** KorgKode reads the module, identifies all code paths, generates test cases, runs them, fixes any failures.

### Demo 4: Dependency Migration (15 minutes)
```bash
korgkode.sh "Upgrade all outdated npm packages to their latest versions and fix any breaking changes"
```
**What you'll see:** KorgKode reads package.json, detects outdated deps, updates versions, handles breaking changes across files, runs the build.

### Demo 5: Full Project Scaffold (10 minutes)
```bash
mkdir my-new-api && cd my-new-api
git init
korgkode.sh "Create a FastAPI project with user authentication, a health endpoint, and pytest test suite"
```
**What you'll see:** KorgKode builds a complete project from scratch — directory structure, config files, source code, tests, README.

### Demo 6: Code Review Pipeline (10 minutes)
```bash
# Create a PR with some issues, then:
korgkode.sh "Review the open PR, check for security issues, and reply to any pending comments"
```
**What you'll see:** KorgKode reads PR comments, analyzes the diff, replies to feedback, pushes fixes.

### Demo 7: Async Multi-Agent (15 minutes)
```bash
# Fire off multiple tasks in parallel
korgkode.sh "Write tests for the auth module" --async &
korgkode.sh "Add input validation to the API layer" --async &
korgkode.sh "Update the README with API documentation" --async &
```
**What you'll see:** Three KorgKode agents work in parallel, each in their own sandbox. Results appear as they complete.

---

## 🔄 Git Workflow

KorgKode follows a structured git workflow inspired by industry best practices:

```
main ──▶ feature/user-auth ──▶ commit ──▶ push ──▶ PR ready
         ▲                    ▲          ▲
         │                    │          │
    KorgKode creates       KorgKode commits   User approves
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
KorgKode generates descriptive commit messages:

```
✅ Good: "fix: correct redirect URL after login — was pointing to /home, now points to /dashboard"
✅ Good: "feat: add /healthz endpoint with JSON status response and test coverage"
❌ Bad:  "fix stuff"
❌ Bad:  "update"
```

### Pre-Commit Checks
Before every commit, KorgKode runs:
1. ✅ Test suite
2. ✅ Linter
3. ✅ Type checker
4. ✅ No debug artifacts
5. ✅ Diff review

---

## 📊 Why KorgKode?

| Capability | KorgKode | GitHub Copilot | Cursor AI | Cody | Claude Code |
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

## 🔒 Enterprise: Zero-Hallucination Runtime

KorgKode is the **first auditable, deterministic agentic runtime**. Every tool call is cryptographically bound to its result.

### The problem

Every other coding agent (Claude Code, Cursor, Copilot, Windsurf) has a fundamental flaw: autoregressive models hallucinate tool results. The model "sees" a successful test run before the tests actually execute, then spirals — generating fixes for bugs that don't exist, committing code that never compiled.

### The solution: Strict Tool Result Pairing

```python
[Model calls Bash("pytest")]
  ↓ tool_use_id = "call_19e57f84191_7449e61c8a1f4120"
[Environment executes pytest]
  ↓ SHA256({tool_use_id}:{result_text}) = "331416869d1e1dd9"
[Next prompt has:]
  Tool Result (call_19e57f84191_7449e61c8a1f4120):
  <actual test output>
```

- Every tool call gets a unique, cryptographically random ID
- Results are paired with their originating ID using SHA256 binding
- The prompt format makes it structurally impossible for the model to fabricate results
- A validation layer scans the conversation and flags unpaired or hallucinated results

### Why enterprise security teams care

| Requirement | KorgKode | Other agents |
|---|---|---|
| **Tamper-evident tool execution** | SHA256 binding between call and result | No binding — model can fabricate |
| **Audit trail** | Every tool call logged with ID, timestamp, duration | Limited or no per-call logging |
| **Blast radius control** | Mode-gated tools — plan mode cannot write files | Mixed, depends on implementation |
| **Deterministic routing** | Every tool has exactly one handler, one schema | Models can guess tool names |
| **Open protocol (MCP)** | Connect any MCP server — no vendor lock-in | Plugin-walled gardens |
| **Open source** | Full source, no binary black boxes | Closed source or partially open |

### Compliance-ready

KorgKode's strict pairing directly addresses requirements emerging from:
- **EU AI Act** — auditable AI decision chains
- **SEC/FINRA** — tamper-evident record keeping
- **SOX** — change management and access controls
- **HIPAA** — verifiable non-repudiation of automated actions

The `validate_prompt_history()` function provides a machine-readable compliance report showing exactly which tool calls were made, what results they returned, and whether any violations were detected.

```bash
# Generate compliance report for any conversation
korgkode audit --session last
# → {"valid": true, "total_results": 47, "violations": []}
```

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
| [KorgKode vs The Rest](docs/comparison.md) | Competitive comparison |

---

## 📄 License

MIT — Own your workflow.