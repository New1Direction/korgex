"""@-file mentions — pull referenced files into a prompt.

Typing ``@path/to/file`` in a prompt inlines that file's contents for the turn, so
you can say "refactor @src/auth.py to use @src/db.py" without pasting. Conservative
by design: only real files are inlined (a bare @handle or an email is left alone),
each is size-capped, and the original instruction text is preserved verbatim — the
file bodies are appended in a clearly-fenced "Referenced files" section so the model
sees both the ask and the code.

Pure (text + cwd + an injectable read hook → expanded text + attached paths), so it
tests without the real filesystem.
"""
from __future__ import annotations

import os
import re

# An @mention: '@' at the start of input or after whitespace (so an email's '@'
# never matches), then a run of path characters.
_MENTION_RE = re.compile(r"(?:^|(?<=\s))@([^\s]+)")
# Trailing punctuation that's almost certainly sentence punctuation, not the path.
_TRAILING = ".,;:!?)]}\"'"

DEFAULT_MAX_BYTES = 100_000


def find_mentions(text: str) -> list:
    """Extract candidate file paths from ``@path`` tokens, de-duped in order."""
    out = []
    for raw in _MENTION_RE.findall(text or ""):
        path = raw.rstrip(_TRAILING)
        if path and path not in out:
            out.append(path)
    return out


def _read(path: str, max_bytes: int):
    """Read up to `max_bytes` of a text file. Returns (content, truncated) or None
    if it isn't a readable regular file."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", errors="replace") as f:
            data = f.read(max_bytes + 1)
    except OSError:
        return None
    if len(data) > max_bytes:
        return data[:max_bytes], True
    return data, False


def expand_mentions(text: str, cwd: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """Inline every ``@file`` that resolves to a real file under `cwd`.

    Returns ``{"text": <expanded>, "attached": [paths...], "missed": [paths...]}``.
    The instruction text is kept verbatim; inlined bodies are appended under a fenced
    "Referenced files" section. ``missed`` lists path-shaped mentions that didn't
    resolve (likely typos) so the caller can flag them; a bare ``@handle`` with no
    path shape is ignored, not reported. With no resolvable mentions the text is
    returned unchanged.
    """
    blocks, attached, missed = [], [], []
    for m in find_mentions(text):
        target = os.path.join(cwd, m) if not os.path.isabs(m) else m
        got = _read(os.path.expanduser(target), max_bytes)
        if got is None:
            if _looks_like_path(m):       # path-shaped but unresolved → likely a typo
                missed.append(m)
            continue                      # bare @handle / a dir → leave it
        content, truncated = got
        note = "  (truncated)" if truncated else ""
        blocks.append(f"## @{m}{note}\n```\n{content}\n```")
        attached.append(m)
    expanded = text if not blocks else (
        text + "\n\n# Referenced files\n\n" + "\n\n".join(blocks))
    return {"text": expanded, "attached": attached, "missed": missed}


def _looks_like_path(m: str) -> bool:
    """Heuristic: does this mention look like a file reference (has a path
    separator or a file extension) vs. an incidental ``@handle``?"""
    return "/" in m or "." in m
