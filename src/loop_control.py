"""Autonomous-loop control for `/loop`.

`/loop <task>` lets korgex grind a task list unattended: it seeds the work, then
after each turn auto-continues while open tasks remain — until the list is empty or
a hard cap stops it. The cap is the runaway guard: a confused model that keeps
"finding more to do" can't burn the API key forever, and Ctrl-C always breaks out
(handled in the REPL).

The continue/stop decision is pure and lives here so it can be tested exhaustively;
the REPL just drives it turn to turn.
"""
from __future__ import annotations

import os

_DEFAULT_MAX = 12


def default_max_iterations() -> int:
    """Max auto-continuations per `/loop` invocation. Override with KORGEX_LOOP_MAX;
    a bad value falls back to the default."""
    raw = os.environ.get("KORGEX_LOOP_MAX", "").strip()
    if raw:
        try:
            n = int(raw)
            if n >= 1:
                return n
        except ValueError:
            pass
    return _DEFAULT_MAX


def should_continue(*, enabled: bool, open_tasks: int, iterations: int,
                    max_iterations: int):
    """Decide whether to auto-run another turn. Returns ``(go, reason)``.

    Order matters: disabled and the cap are hard stops checked before "work
    remains", so the guard can never be talked past by a model insisting there's
    more to do.
    """
    if not enabled:
        return False, "loop off"
    if iterations >= max_iterations:
        return False, f"hit loop cap ({max_iterations}) — stopping to be safe"
    if open_tasks <= 0:
        return False, "all tasks done"
    return True, ""


def seed_prompt(task: str) -> str:
    """The first turn of a `/loop`: the user's task plus a nudge to externalize it
    as a task list, so the grind has concrete items to drive on."""
    return (
        f"{task}\n\n(Autonomous mode: break this into a task list with TaskCreate, "
        "then work through every item, keeping it current with TaskUpdate. You'll be "
        "auto-continued until all tasks are complete — don't stop while work remains.)"
    )


CONTINUE_PROMPT = (
    "Continue working through your task list — complete the next open item and mark "
    "it done with TaskUpdate. Do not stop while tasks remain open."
)
