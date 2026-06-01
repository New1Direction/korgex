"""Typed stall classifier — diagnose WHICH kind of stuck a run is.

The loop rails (src/loop_guard) *react* — block a repeat, nudge a non-action.
This *diagnoses*: it turns the round's signals into a typed verdict so the agent
and operator can tell a genuinely-busy run from a stuck one, and name the failure
mode. The highest-value verdict is `false_completion` — the model claims "done"
but produced no deliverable for a task that expected one (the case where an
operator would otherwise walk away believing work finished).

Categories:
  working          — a tool ran; real progress (benign)
  complete         — claimed done, and either produced the expected artifact or
                     the task expected none (benign)
  looping          — the same call keeps failing (a doom loop)
  narrating        — stated an intent to act but called no tool
  asking           — asked the user a question instead of acting
  false_completion — claimed done but produced no expected deliverable

Pure + deterministic (no LLM), so it's cheap to run every turn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LOOP_THRESHOLD = 5  # consecutive identical failures = a loop

_COMPLETE_RE = re.compile(
    r"\b(all done|done\b|task is (complete|done)|finished|completed|"
    r"that'?s (it|all|everything)|everything (works|passes|is done))",
    re.IGNORECASE,
)
_INTENT_RE = re.compile(
    r"\b(let me|i'?ll|i will|i'?m going to|i am going to|going to|let's)\s+"
    r"(search|look|read|check|run|grep|find|edit|write|open|inspect|examine|explore|test|fetch)\b",
    re.IGNORECASE,
)


def claims_complete(text: str) -> bool:
    """True if the text asserts the task is finished."""
    return bool(text) and bool(_COMPLETE_RE.search(text))


def is_question(text: str) -> bool:
    """True if the text is (or ends in) a question to the user."""
    if not text:
        return False
    t = text.strip()
    if t.endswith("?"):
        return True
    return bool(re.search(r"\b(should i|which|do you want|shall i|can you confirm|"
                          r"would you like)\b.*\?", t, re.IGNORECASE))


def _states_intent(text: str) -> bool:
    return bool(text) and bool(_INTENT_RE.search(text))


@dataclass
class Signals:
    """What we observed about one agent round."""
    text: str = ""
    had_tool_call: bool = False
    repeat_streak: int = 0       # consecutive identical-failure count (from RepeatGuard)
    produced_artifact: bool = False   # did this run mutate/produce a deliverable?
    expects_artifact: bool = False    # does the task call for a deliverable?


@dataclass
class Verdict:
    category: str
    reason: str
    confidence: float

    _STUCK = {"looping", "narrating", "asking", "false_completion"}

    def is_stuck(self) -> bool:
        return self.category in self._STUCK


def classify(sig: Signals) -> Verdict:
    """Diagnose the round. Precedence: a confirmed loop dominates; then a
    completion CLAIM is checked for substance (artifact); then non-action modes
    (narrating/asking); else working."""
    # A loop is the strongest stuck signal — even if a tool "ran", it keeps failing.
    if sig.repeat_streak >= _LOOP_THRESHOLD:
        return Verdict("looping", f"the same call has failed {sig.repeat_streak} times in a row",
                       0.95)

    if claims_complete(sig.text):
        # The highest-value catch: "done" with nothing delivered on a task that
        # expected a deliverable.
        if sig.expects_artifact and not sig.produced_artifact:
            return Verdict("false_completion",
                           "claimed done but produced no deliverable for the task", 0.8)
        return Verdict("complete", "claimed done with the expected result", 0.9)

    if sig.had_tool_call:
        return Verdict("working", "a tool ran — real progress this round", 0.9)

    # No tool call and not a completion claim → a stalled non-action.
    if is_question(sig.text):
        return Verdict("asking", "asked the user a question instead of acting", 0.7)
    if _states_intent(sig.text):
        return Verdict("narrating", "stated an intent to act but called no tool", 0.7)

    # A no-action round on a task that expects no deliverable is an ANSWER — the
    # agent has nothing left to do (e.g. a question answered). That's complete.
    if not sig.expects_artifact and sig.text.strip():
        return Verdict("complete", "answered; the task expected no deliverable", 0.7)

    # Text with no action, no claim, no question on an artifact-expecting task —
    # weakly 'working' (e.g. mid-reasoning before the next tool call).
    return Verdict("working", "no action this round", 0.4)
