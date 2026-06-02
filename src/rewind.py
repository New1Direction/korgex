"""Session rewind — undo file edits back to an earlier prompt.

Each turn, the FIRST time the agent touches a file, we record that file's
start-of-turn content (or ``None`` if it didn't exist yet). Rewinding "to turn N"
restores every file to the state it had when turn N began: write back the recorded
content, or delete files that were created at/after N. Pairs with the agent's
existing pre-content capture; this just accumulates it per turn for the live repo.

The restore computation is pure (``compute_restore``); file writes go through an
injected ``writer`` so it's testable without a real filesystem.
"""
from __future__ import annotations

import difflib
import os
from dataclasses import dataclass


@dataclass
class TurnPoint:
    turn: int
    prompt: str


def compute_restore(records, target_turn: int) -> dict:
    """Given ``(turn, path, pre_content)`` records, return ``{path: content_or_None}``
    to restore each file to its state at the START of ``target_turn``. For each path
    that's the earliest recorded pre-state at or after ``target_turn`` (None ⇒ the
    file didn't exist then, so it should be deleted)."""
    by_path: dict = {}
    for (turn, path, pre) in records:
        if turn >= target_turn and (path not in by_path or turn < by_path[path][0]):
            by_path[path] = (turn, pre)
    return {path: pre for path, (_t, pre) in by_path.items()}


def line_delta(pre, post) -> tuple:
    """(added, removed) line counts between two file states. ``None`` means the file
    didn't exist (so created → all additions, deleted → all removals)."""
    a = (pre or "").splitlines()
    b = (post or "").splitlines()
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "replace":
            removed += i2 - i1
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "insert":
            added += j2 - j1
    return added, removed


def summarize_changes(records, read_fn) -> list:
    """Turn ``[(path, pre_content)]`` + a ``read_fn(path)->current_or_None`` into a
    per-file change list: ``[{path, kind, added, removed}]``. ``kind`` is
    created/deleted/modified; files with no net change are dropped."""
    out = []
    for path, pre in records:
        post = read_fn(path)
        added, removed = line_delta(pre, post)
        if added == 0 and removed == 0:
            continue
        kind = "created" if pre is None else ("deleted" if post is None else "modified")
        out.append({"path": path, "kind": kind, "added": added, "removed": removed})
    return out


def render_change_summary(items) -> str:
    """A one-line ``✎ changed N file(s): path (+a -b), …`` summary. Empty when
    nothing changed, so the caller can skip printing."""
    if not items:
        return ""
    parts = [f"{it['path']} (+{it['added']} -{it['removed']})" for it in items]
    n = len(items)
    return f"✎ changed {n} file{'s' if n != 1 else ''}: " + ", ".join(parts)


def _default_writer(path: str, pre_content):
    """Restore a single file: write its prior content, or delete if it was created."""
    try:
        if pre_content is None:
            if os.path.exists(path):
                os.remove(path)
            return "deleted"
        with open(path, "w") as f:
            f.write(pre_content)
        return "restored"
    except OSError:
        return "failed"


class RewindLog:
    """Per-turn snapshots of files the agent modified, for an undo-to-prompt."""

    def __init__(self):
        self._records: list = []        # (turn, path, pre_content)
        self._seen: set = set()         # (turn, path) — keep first state only
        self._prompts: dict = {}        # turn -> prompt text

    def begin_turn(self, turn: int, prompt: str) -> None:
        self._prompts[turn] = prompt

    def record_pre(self, turn: int, path: str, pre_content) -> None:
        """Record a file's start-of-turn content the first time it's touched."""
        key = (turn, path)
        if key in self._seen:
            return
        self._seen.add(key)
        self._records.append((turn, path, pre_content))

    def records_for_turn(self, turn: int) -> list:
        """The ``(path, pre_content)`` snapshots captured during `turn`, in order —
        the basis for that turn's change summary."""
        return [(p, c) for (t, p, c) in self._records if t == turn]

    def points(self) -> list:
        """Turns that have snapshots, in order, with their prompts."""
        turns = sorted({t for (t, _p, _c) in self._records})
        return [TurnPoint(t, self._prompts.get(t, "")) for t in turns]

    def plan_restore(self, target_turn: int) -> dict:
        return compute_restore(self._records, target_turn)

    def restore(self, target_turn: int, writer=_default_writer) -> list:
        """Apply the restore; returns [(path, action)]."""
        return [(path, writer(path, pre))
                for path, pre in self.plan_restore(target_turn).items()]

    def forget_from(self, target_turn: int) -> None:
        """Drop snapshots at/after target_turn (after a rewind, that future is gone)."""
        self._records = [(t, p, c) for (t, p, c) in self._records if t < target_turn]
        self._seen = {(t, p) for (t, p, _c) in self._records}
        self._prompts = {t: v for t, v in self._prompts.items() if t < target_turn}
