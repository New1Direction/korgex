# Reviewing and Steering the Agent

korgex runs autonomously — you give it a task and it works until done. This page covers how to see what it's doing, catch it going wrong, and course-correct without starting over.

## Watching the TUI

When stdout is a TTY, korgex streams its work live:

- **Thinking** (Anthropic only) renders in dimmed italic — this is the agent's internal reasoning before tool calls
- **Tool calls** show as transient spinners: `⠋ Edit(file_path=src/foo.py, ...)`
- **Tool results** resolve to one-line summaries: `✓ Bash — 3 passed in 0.4s`
- **Final text** streams character by character after all tool calls are done

Press **Ctrl+C** once for a graceful interrupt. The agent gets a chance to wrap up the current tool call. Press twice to force-kill.

## Quiet mode for scripts

```bash
korgex --quiet "list all exported functions in src/"
```

Suppresses the TUI entirely. The final result text is printed to stdout on exit. Exit code is 0 on success, 1 if the agent hit max iterations without finishing, 2 for configuration errors.

## Using the dashboard

`korgex serve` starts a FastAPI dashboard at `http://127.0.0.1:8090` by default with:

- **Current task and plan** — what the agent is working on
- **Live log stream** — `/ws/logs` WebSocket
- **Approve plan** — POST `/api/approve-plan`
- **Send feedback** — POST `/api/send-feedback` with `{"feedback": "..."}` to inject a steering message mid-task
- **Start a new task** — POST `/api/new-task` with `{"description": "..."}`
- **Sandbox status** — GET `/api/sandbox`

The VS Code sidecar extension uses the same API — commands like "Korgex: Refactor Current File" POST to `/api/swarm/refactor`. The dashboard is not authenticated; set `KORGEX_DASHBOARD_HOST=0.0.0.0` only behind an auth-terminating proxy.

## When to intervene

The agent is designed to solve tasks autonomously. But some situations benefit from a nudge:

**Agent is reading files that aren't relevant** — this is normal early exploration. Give it a few iterations; it usually focuses quickly. If it's still wandering after 5-10 tool calls, kill it and add more specificity to your prompt.

**Agent is about to make a change you don't want** — use Ctrl+C, check `git diff`, then re-run with a more constrained prompt: `"only change src/auth/middleware.py, don't touch the tests"`.

**Agent hits max iterations** — exits with code 1 and prints the partial result. Review `git diff`, see how far it got, then run again with a narrower task: `"continue from where you left off — the tests are still failing in test_auth.py"`.

**Agent asks a question** — it used `AskUserQuestion`. Answer in the TUI prompt and it continues.

## Plan-first mode

For architectural or design questions, use `--mode plan`:

```bash
korgex --mode plan "how should we add rate limiting to the API?"
```

This uses Opus with extended thinking. The agent does read-only analysis and writes a structured plan — it won't edit files. Use the output to inform your next `--mode execute` run.

## Approving work

korgex doesn't auto-commit or auto-push. After a run:

```bash
git diff          # review all changes
git add -p        # stage selectively if needed
git commit -m "..."
```

The agent may create files, edit existing ones, or run test commands — but it never touches your git history unless you explicitly ask it to (`korgex "commit these changes with a descriptive message"`).
