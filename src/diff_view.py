"""Inline diff rendering — see exactly what the agent changed.

A colored unified diff per file (the CC/Cursor feel), built straight from the
before/after content korgex already tracks via the rewind log. Deliberately uses
ANSI escapes, not rich markup: diff bodies are full of ``[`` and other markup
metacharacters, and ANSI sidesteps all of that. Hunks are capped so a giant edit
doesn't flood the terminal, and an unchanged file renders nothing.
"""
from __future__ import annotations

import difflib

_GREEN = "\033[32m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def render_unified_diff(path: str, before, after, *, max_lines: int = 80,
                        context: int = 3, color: bool = True) -> str:
    """A unified diff of one file. ``before``/``after`` are strings (``None`` ⇒
    didn't exist). Returns "" when nothing changed. Capped at ``max_lines`` body
    lines with a truncation note; the path heads the output."""
    a = (before or "").splitlines()
    b = (after or "").splitlines()
    diff = list(difflib.unified_diff(a, b, lineterm="", n=context))
    if not diff:
        return ""
    # Drop the ---/+++ file headers (we print the path ourselves); keep @@ + body.
    body = [ln for ln in diff if not (ln.startswith("---") or ln.startswith("+++"))]
    truncated = len(body) > max_lines
    if truncated:
        body = body[:max_lines]

    def paint(ln: str) -> str:
        if not color:
            return ln
        if ln.startswith("+"):
            return f"{_GREEN}{ln}{_RESET}"
        if ln.startswith("-"):
            return f"{_RED}{ln}{_RESET}"
        if ln.startswith("@@"):
            return f"{_CYAN}{ln}{_RESET}"
        return f"{_DIM}{ln}{_RESET}"

    head = f"{_DIM}{path}{_RESET}" if color else path
    lines = [head] + [paint(ln) for ln in body]
    if truncated:
        note = f"… diff truncated at {max_lines} lines"
        lines.append(f"{_DIM}{note}{_RESET}" if color else note)
    return "\n".join(lines)


def render_turn_diffs(records, read_fn, *, color: bool = True, max_lines: int = 80) -> str:
    """Render diffs for every file in a turn's rewind ``[(path, pre_content)]``
    records (``read_fn(path)`` gives the current content). Files with no change are
    skipped; "" when nothing changed."""
    blocks = []
    for path, pre in records:
        d = render_unified_diff(path, pre, read_fn(path), color=color, max_lines=max_lines)
        if d:
            blocks.append(d)
    return "\n\n".join(blocks)
