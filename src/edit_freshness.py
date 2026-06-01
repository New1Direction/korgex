"""Stale-file detection for safe edits.

Tracks a content hash per file the moment the agent reads it. Before a mutating
tool (Edit/Write) runs, we compare the file's current hash to that baseline: if it
changed out-of-band since the read, the edit would clobber someone else's change —
so we refuse and tell the agent to re-Read.

The baseline is refreshed after korgex's OWN successful writes, so its own edits
never read as stale. Hash (not mtime) so a touch-without-change doesn't false-trip
and a same-mtime change isn't missed. Best-effort + in-process (one session).
"""
from __future__ import annotations

import hashlib
import os

_READ_HASHES: dict = {}  # abspath -> sha256 hex of content when last read/written by us


def _hash(path: str):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def record_read(path: str) -> None:
    """Set the freshness baseline for `path` to its current content (call on Read,
    and after a successful Write/Edit so our own change isn't seen as stale)."""
    h = _hash(path)
    if h is not None:
        _READ_HASHES[os.path.abspath(path)] = h


def check_fresh(path: str):
    """Return ``(status, reason)``: ``new`` (doesn't exist yet), ``unknown`` (never
    read — no baseline), ``stale`` (changed since read → refuse), or ``ok``."""
    ap = os.path.abspath(path)
    if not os.path.exists(ap):
        return ("new", "")
    baseline = _READ_HASHES.get(ap)
    if baseline is None:
        return ("unknown", "")
    if _hash(ap) != baseline:
        return ("stale", f"{os.path.basename(path)} changed on disk since you last read it — "
                          f"Read it again before editing (your edit would overwrite that change).")
    return ("ok", "")


def reset() -> None:
    """Clear all baselines (e.g. on /clear)."""
    _READ_HASHES.clear()
