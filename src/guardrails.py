"""
guardrails.py — protect korgex's own gate-enforcing code (roadmap Gate G).

A self-modifying agent must not be able to weaken the very code that enforces
its safety gates (isolation, the test gate, the ledger, the eval). Otherwise a
single bad self-edit could disable a check and then ship — the objective-hacking
failure observed in self-improving agents. Gate G fences the guardrail-critical
files two ways:

  • is_protected / classify_diff — the MERGE GATE: any diff touching a protected
    path is flagged human-required (never auto-mergeable).
  • (wired in agent.py) an in-run PreToolUse block — a Write/Edit to a protected
    file during an unsupervised run is blocked and recorded as a PROTECTED_PATH
    verdict on the ledger.

These files stay OUTSIDE the autonomously-modifiable surface; edits to them
require explicit human approval even at the unsupervised "run" stage.
"""

from __future__ import annotations

import os

# The gate-enforcing surface (Gates A–F) + the eval. Edits here are never
# auto-merged. Repo-relative paths; basename also matches (worktree-safe).
DEFAULT_PROTECTED = [
    "src/agent.py",          # the loop + gate wiring
    "src/korg_ledger.py",    # ledger + rewind (Gate C/D)
    "src/hooks.py",          # PreToolUse/PostToolUse (Gate B substrate)
    "src/workspace.py",      # isolation + checkpoint (Gate A/C)
    "src/test_gate.py",      # the verification gate (Gate B)
    "src/sandbox.py",        # sandbox/isolation layer
    "src/korgex_bench.py",   # the eval harness / oracle (Gate E)
    "src/guardrails.py",     # this fence itself
    ".korgex/settings.json", # hook + test-gate config
]


def _norm(p: str) -> str:
    return p.replace("\\", "/").lstrip("./")


def is_protected(path: str, patterns=DEFAULT_PROTECTED) -> bool:
    """True if `path` is a guardrail-critical file. Matches the repo-relative
    path, any absolute path ending in it, or the bare basename."""
    n = _norm(path)
    base = os.path.basename(n)
    for pat in patterns:
        pn = _norm(pat)
        if n == pn or n.endswith("/" + pn) or base == os.path.basename(pn):
            return True
    return False


def classify_diff(changed_paths, patterns=DEFAULT_PROTECTED) -> dict:
    """The merge gate: classify a set of changed paths.

    Returns {protected_hits, requires_human_review, auto_mergeable}. A diff is
    auto-mergeable only if it touches NO protected path.
    """
    hits = [p for p in changed_paths if is_protected(p, patterns)]
    return {
        "protected_hits": hits,
        "requires_human_review": bool(hits),
        "auto_mergeable": not hits,
    }
