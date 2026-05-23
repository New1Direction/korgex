# Seluj — Autonomous AI Software Engineer

"Jules" spelled backwards. A standalone clone of Google Jules' architecture.

## Architecture

```
User Prompt → EXPLORE (read AGENTS.md, README, codebase)
            → PLAN (set_plan, numbered markdown steps)
            → APPROVE (record_user_approval_for_plan)
            → EXECUTE (tool chain: read/write/bash/search)
            → VERIFY (read_file, list_files, run tests)
            → PRE_COMMIT (pre_commit_instructions)
            → SUBMIT (branch, commit, PR)
            → REVIEW (request/reply code review)
```

## Components

- `src/` — Core agent loop, tool dispatch, planning
- `tools/` — 33 tool handlers
- `cli/` — Command-line interface
- `api/` — REST API server
- `sandbox/` — Execution environment (Docker)
- `memory/` — Cross-session memory store
- `agents/` — Subagent system
- `frontend/` — Playwright verification scripts
- `plans/` — Plan storage and versioning
- `tests/` — Test suite

## Quick Start

```bash
pip install -r requirements.txt
python -m cli.main "fix the bug in src/main.py"
```

## Environment

- Python 3.12+
- Docker (for sandbox)
- Node.js 22+ (for frontend verification)
- git 2.49+
- Playwright (for frontend tests)