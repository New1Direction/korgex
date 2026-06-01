"""Plan mode — propose a plan, get approval, then execute.

The cheap, reversible decision (was the *approach* right?) should come before the
expensive, hard-to-undo work. In plan mode the agent runs **read-only**: it may
read/search and write its plan to a single plan file, but every other
side-effecting tool (Edit, arbitrary Write, Bash, …) is blocked until the user
**approves**. The user can approve / revise / abandon.

Two pure pieces, both testable with no agent loop:
  - `is_blocked(tool, args, plan_path)` — the read-only gate (None = allowed).
  - `PlanState` + `parse_approval` — the planning→executing/abandoned lifecycle.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Tools that change the world. In plan mode they're blocked (except a Write to
# the plan file). Everything not here — Read, Grep, Glob, ToolSearch, Recall,
# BusInbox, etc. — is read-only and allowed.
SIDE_EFFECTING = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "BusSend"}
_PATH_KEYS = ("file_path", "filepath", "path", "notebook_path")


def _arg_path(args: dict):
    if not isinstance(args, dict):
        return None
    for k in _PATH_KEYS:
        if args.get(k):
            return args[k]
    return None


def _same_file(a: str, b: str) -> bool:
    """True if two paths point at the same file (basename-tolerant: an absolute
    plan path matches a relative write to the same file and vice-versa)."""
    if not a or not b:
        return False
    if os.path.normpath(a) == os.path.normpath(b):
        return True
    return os.path.basename(a) == os.path.basename(b)


def is_blocked(tool_name: str, args: dict, plan_path: str) -> dict | None:
    """Return a block-result dict if `tool_name` is forbidden in plan mode, else
    None. The ONLY permitted mutation is writing the plan file itself."""
    if tool_name not in SIDE_EFFECTING:
        return None  # read-only tool → always fine
    if tool_name == "Write" and _same_file(_arg_path(args) or "", plan_path):
        return None  # writing the plan is how the agent works in plan mode
    return {
        "error": "blocked in plan mode (read-only)",
        "reason": (f"{tool_name} can't run while planning — propose the change in your plan "
                   f"({os.path.basename(plan_path)}), then ask the user to approve to execute."),
    }


# ── lifecycle ───────────────────────────────────────────────────────────────

_APPROVE = {"approve", "a", "yes", "y", "ok"}
_REVISE = {"revise", "r", "edit", "change"}
_ABANDON = {"abandon", "q", "quit", "cancel", "stop", "no", "n"}


def parse_approval(line: str):
    """Map a user's reply to approve|revise|abandon, or None if unrecognized.
    Matches on the first word, so 'revise the test step' → 'revise'."""
    head = (line or "").strip().lower().split(" ")[0]
    if head in _APPROVE:
        return "approve"
    if head in _REVISE:
        return "revise"
    if head in _ABANDON:
        return "abandon"
    return None


@dataclass
class PlanState:
    """planning → (approve)→ executing | (abandon)→ abandoned. revise stays planning."""
    phase: str = "planning"

    def apply(self, action: str) -> str:
        if action == "approve":
            self.phase = "executing"
        elif action == "abandon":
            self.phase = "abandoned"
        # "revise" or anything unrecognized → stay in planning
        return self.phase

    def is_planning(self) -> bool:
        return self.phase == "planning"

    def is_executing(self) -> bool:
        return self.phase == "executing"
