# Getting Started

Seluj is an autonomous coding agent that helps you fix bugs, write tests, build features, and refactor code. It integrates with GitHub, understands your codebase, and works **autonomously** — so you can move on while it handles the task.

This guide will walk you through setting up Seluj and running your first task.

## Installation

Seluj runs as a Python CLI tool. Clone the repository and install dependencies:

```bash
git clone https://github.com/New1Direction/Seluj.git
cd Seluj
pip install -r requirements.txt
```

## Setting Up Your Repository

Seluj works best with a git repository. Navigate to your project and initialize Seluj:

```bash
cd /path/to/your/project
python /path/to/Seluj/seluj.sh --init
```

This creates an `AGENTS.md` file in your repository root. Seluj reads this file to understand your project conventions, build commands, and testing patterns.

## Running Your First Task

Once set up, you're ready to delegate work to Seluj.

```bash
python /path/to/Seluj/seluj.sh "Add unit tests for the authentication module"
```

Seluj will:

1. **Explore** your codebase — reading files, understanding structure
2. **Plan** — formulate a step-by-step markdown plan
3. **Present** the plan for your approval
4. **Execute** — write code, run tests, verify changes
5. **Submit** — create a branch and commit when complete

## Authentication

Seluj connects to an LLM backend to power its reasoning. Set your API credentials:

```bash
export SELUJ_API_KEY="sk-your-key-here"
export SELUJ_MODEL="claude-sonnet-4"  # or any supported model
export SELUJ_API_URL="https://api.openai.com/v1"  # your provider endpoint
```

## What's Next

- [Running Tasks](/docs/running-tasks) — Full walkthrough
- [Environment Setup](/docs/environment) — Make Seluj smarter about your project
- [Reviewing Plans & Feedback](/docs/review-plan) — Approve and guide Seluj
- [CLI Reference](/docs/cli-reference) — All commands and options