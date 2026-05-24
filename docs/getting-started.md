# Getting Started

Korgex is an autonomous coding agent that helps you fix bugs, write tests, build features, and refactor code. It integrates with GitHub, understands your codebase, and works **autonomously** — so you can move on while it handles the task.

This guide will walk you through setting up Korgex and running your first task.

## Installation

Korgex runs as a Python CLI tool. Clone the repository and install dependencies:

```bash
git clone https://github.com/New1Direction/Korgex.git
cd Korgex
pip install -r requirements.txt
```

## Setting Up Your Repository

Korgex works best with a git repository. Navigate to your project and initialize Korgex:

```bash
cd /path/to/your/project
python /path/to/Korgex/korgex.sh --init
```

This creates an `AGENTS.md` file in your repository root. Korgex reads this file to understand your project conventions, build commands, and testing patterns.

## Running Your First Task

Once set up, you're ready to delegate work to Korgex.

```bash
python /path/to/Korgex/korgex.sh "Add unit tests for the authentication module"
```

Korgex will:

1. **Explore** your codebase — reading files, understanding structure
2. **Plan** — formulate a step-by-step markdown plan
3. **Present** the plan for your approval
4. **Execute** — write code, run tests, verify changes
5. **Submit** — create a branch and commit when complete

## Authentication

Korgex connects to an LLM backend to power its reasoning. Set your API credentials:

```bash
export KORGEX_API_KEY="sk-your-key-here"
export KORGEX_MODEL="claude-sonnet-4"  # or any supported model
export KORGEX_API_URL="https://api.openai.com/v1"  # your provider endpoint
```

## What's Next

- [Running Tasks](/docs/running-tasks) — Full walkthrough
- [Environment Setup](/docs/environment) — Make Korgex smarter about your project
- [Reviewing Plans & Feedback](/docs/review-plan) — Approve and guide Korgex
- [CLI Reference](/docs/cli-reference) — All commands and options