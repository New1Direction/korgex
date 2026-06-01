"""Loop safety rails — deterministic guards against two agent-loop pathologies.

`RepeatGuard` — the agent retries the SAME failing tool call over and over. It
hashes each ``(name, args)`` and tracks the consecutive-identical-FAILURE streak:
at `warn_at` it returns a warning (the caller injects a nudge), at `force_at` it
returns "force" (the caller blocks the repeat and tells the model to change tack).
A different call, or a success, resets the streak.

`IntentGuard` + `looks_like_unacted_intent` — the model says "let me search…" but
emits no tool call ("narrating instead of acting"). The caller nudges it to
actually call the tool, capped so the nudge itself can't loop.

Both are pure and LLM-free, so they're cheap and fully testable.
"""
from __future__ import annotations

import json
import re

# Phrases that state an *intent to act* (which should be a tool call), as opposed
# to reporting a result. Matched case-insensitively against the round's text.
_INTENT_RE = re.compile(
    r"\b(let me|i'?ll|i will|i'?m going to|i am going to|going to|let's)\s+"
    r"(search|look|read|check|run|grep|find|edit|write|open|inspect|examine|explore|test|fetch)\b",
    re.IGNORECASE,
)


def _key(name: str, args: dict) -> str:
    """A stable hash of a tool call — order-independent over args."""
    try:
        a = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        a = str(args)
    return f"{name}|{a}"


class RepeatGuard:
    """Tracks the consecutive identical-FAILURE streak of tool calls."""

    def __init__(self, warn_at: int = 3, force_at: int = 5):
        self.warn_at = warn_at
        self.force_at = force_at
        self._last = None
        self._streak = 0

    def check(self, name: str, args: dict, *, failed: bool) -> str:
        """Record one tool call's outcome and return a verdict:
        ``"ok"`` | ``"warn: …"`` | ``"force"``.

        A success, or any call that differs from the last, resets the streak —
        only an unbroken run of the *same failing* call escalates."""
        k = _key(name, args)
        if not failed or k != self._last:
            self._last = k if failed else None
            self._streak = 1 if failed else 0
            return "ok"
        # same call, failed again
        self._streak += 1
        if self._streak >= self.force_at:
            return "force"
        if self._streak >= self.warn_at:
            return (f"warn: this is attempt {self._streak} of the same failing call "
                    f"({name}). Try a different approach.")
        return "ok"


def looks_like_unacted_intent(text: str) -> bool:
    """True if `text` states an intent to take a tool action but (by virtue of being
    a no-tool-call round) didn't. Used only when the round produced NO tool calls."""
    if not text:
        return False
    return bool(_INTENT_RE.search(text))


class IntentGuard:
    """Caps how many times we nudge a model that narrates instead of acting, so the
    nudge itself can't become a loop."""

    def __init__(self, max_nudges: int = 2):
        self.max_nudges = max_nudges
        self._count = 0

    def nudge(self):
        """Return a nudge message while under the cap, else None (stop nudging)."""
        if self._count >= self.max_nudges:
            return None
        self._count += 1
        return ("You described an action but didn't call a tool. If you intend to act, "
                "call the tool now; otherwise give your final answer.")
