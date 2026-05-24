# Running Tasks

Once korgex is installed and an API key is set, you're ready. This guide covers writing good prompts and understanding what the agent does with them.

## Writing a good prompt

Specific and scoped works best. Plain language is fine — no special syntax required.

**Good prompts**

```bash
korgex "fix the 500 error when submitting the feedback form in src/api/feedback.py"
korgex "add unit tests for the parse_config function in src/config.py"
korgex "bump requests from 2.28 to 2.32 and fix any breaking changes"
korgex "document the useCache hook with JSDoc — include param types and return type"
```

**Avoid**

```
korgex "fix everything"
korgex "optimize the code"
korgex "make this better"
```

Vague prompts make the agent guess scope. If it's unsure, it uses `AskUserQuestion` to ask before starting.

## What the agent does

### 1. Explore

The agent reads context before touching anything:

```
Read(file_path=README.md)
Read(file_path=AGENTS.md)
Glob(pattern=src/**/*.py)
Read(file_path=src/api/feedback.py)
```

### 2. Plan

The agent forms an internal plan — what files to change, what to verify, in what order. If you're watching the TUI, you'll see this as thinking output (Anthropic) or initial text before tool calls begin.

### 3. Execute

The agent makes changes using surgical edits:

```
Edit(file_path=src/api/feedback.py, old_string="...", new_string="...")
Bash(command="pytest tests/test_feedback.py -q")
Read(file_path=src/api/feedback.py)   ← verifies the edit applied
```

Every file it writes or edits, it reads back to confirm.

### 4. Report

When the agent has no more tool calls to make, it returns a summary of what was done. In TUI mode this streams live; with `--quiet` it prints at the end.

## Monitoring progress

The streaming TUI shows each tool call as it happens:

```
➤ Read(file_path=src/api/feedback.py)
➤ Edit(file_path=src/api/feedback.py, ...)
⠋ Bash(command=pytest tests/test_feedback.py -q)
✓ Bash — 3 passed in 0.4s
```

Use `--quiet` in scripts or CI to suppress the TUI and get only the final result.

## Steering mid-task

The agent can be interrupted with Ctrl+C (graceful) or Ctrl+C twice (force). For mid-task feedback during dashboard use, POST to `/api/send-feedback`.

## Iterating

If the result isn't what you wanted, run korgex again with a more specific prompt. Each invocation is a fresh session — the agent re-reads the current state of the files, so it works from whatever changes the previous run made.

## Task scope tips

- **One concern per run** works best. The agent is good at "fix X" or "add Y" — less reliable on "fix X, add Y, and also refactor Z".
- **Point at the file** if you know where the change lives: `"fix the auth bug in src/auth/middleware.py"` is faster than `"fix the auth bug"`.
- **Use `--mode plan`** for architectural questions: `korgex --mode plan "how should we add multi-tenancy to the billing module?"`. Opus with extended thinking; read-only analysis.
- **Use `--mode debug`** for tracing errors: `korgex --mode debug "why is /api/users returning 403 for valid tokens?"`. Haiku; fast and focused.
