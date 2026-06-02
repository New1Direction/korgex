"""Live task ledger — the agent's self-updating checklist.

This is the steering mechanism that makes an agent feel like it's *working through*
a task instead of free-associating: it writes a list, marks each item
in_progress/completed as it goes, the list is rendered for the user, and — crucially
— it's fed back into the agent's context every turn so the model sees its own open
obligations and can't drift or claim done while items remain.

Pure + side-effect-free; the agent loop owns an instance and the REPL renders it.
"""
from __future__ import annotations

from dataclasses import dataclass

_SYMBOL = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
VALID_STATUS = ("pending", "in_progress", "completed")


@dataclass
class Task:
    id: int
    text: str
    status: str = "pending"


class TaskLedger:
    def __init__(self):
        self._tasks: list = []

    def set_tasks(self, texts) -> list:
        """Replace the list with fresh pending tasks, numbered from 1."""
        self._tasks = [Task(i + 1, str(t)) for i, t in enumerate(texts or [])]
        return list(self._tasks)

    def _find(self, ref):
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            rid = int(ref)
            return next((t for t in self._tasks if t.id == rid), None)
        return next((t for t in self._tasks if t.text == ref), None)

    def update(self, ref, status: str):
        """Set a task's status (by id, numeric string, or exact text). Returns the
        Task, or None if the ref/status is unknown."""
        if status not in VALID_STATUS:
            return None
        t = self._find(ref)
        if t is None:
            return None
        t.status = status
        return t

    def tasks(self) -> list:
        return list(self._tasks)

    def open_tasks(self) -> list:
        return [t for t in self._tasks if t.status != "completed"]

    def render(self) -> str:
        """A checklist for the user / for feeding back to the agent. Empty if none."""
        if not self._tasks:
            return ""
        return "\n".join(f"  {_SYMBOL.get(t.status, '[ ]')} {t.text}" for t in self._tasks)

    def summary(self) -> str:
        if not self._tasks:
            return "no tasks"
        done = sum(1 for t in self._tasks if t.status == "completed")
        return f"{done}/{len(self._tasks)} done"

    def all_done(self) -> bool:
        return bool(self._tasks) and all(t.status == "completed" for t in self._tasks)
