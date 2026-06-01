"""Model-authored context compaction — summarize-and-continue for long runs.

A long agent run grows its message history toward the model's context window and
eventually fails. Mechanical truncation loses intent; instead, when the transcript
gets large the MODEL writes its own structured handoff summary (progress, key
decisions, constraints, remaining steps), and history is rebuilt as:

    [head]  +  [most-recent raw turns, back-filled to a token budget]  +  [summary]

so the immediate task stays verbatim while stale middle history collapses into the
summary, and the run continues. The summary call is injected (`summarize`) so this
is testable with no network, and it FAILS SAFE: if summarization errors, the
original history is returned untouched — a long run never loses its conversation.
"""
from __future__ import annotations

# Rough chars-per-token; good enough for a trigger heuristic (we don't need exact
# tokenization to decide "the transcript is getting big").
_CHARS_PER_TOKEN = 4

# Fraction of the model's context window at which we compact (leave headroom for
# the next request + its response).
_TRIGGER_FRACTION = 0.75

# Don't compact a transcript with essentially nothing to summarize (a single
# short turn). Measured in tokens, not turn count — one giant message still
# warrants compaction, while a couple of tiny turns never does.
_MIN_TOKENS_TO_COMPACT = 200

_RESUME_PREAMBLE = (
    "[Earlier conversation was summarized to save context. Another step produced "
    "the summary below — build on it; do not redo completed work.]\n\n"
)


def _content_len(msg: dict) -> int:
    c = msg.get("content", "")
    if isinstance(c, str):
        return len(c)
    # tool-result / structured content → stringify for a length estimate
    return len(str(c))


def estimate_tokens(messages: list) -> int:
    """Rough token estimate for a message list (chars / 4)."""
    return sum(_content_len(m) for m in messages) // _CHARS_PER_TOKEN


def should_compact(messages: list, limit_tokens: int) -> bool:
    """True when the transcript is big enough to compact: over the trigger fraction
    of the context window AND there's enough content to be worth summarizing."""
    tokens = estimate_tokens(messages)
    if tokens < _MIN_TOKENS_TO_COMPACT:
        return False
    return tokens >= int(limit_tokens * _TRIGGER_FRACTION)


def _recent_within_budget(history: list, recent_budget_tokens: int) -> list:
    """Walk history newest→oldest, keeping turns until the token budget fills; return
    them in original order. Always keeps at least the most recent turn."""
    budget = recent_budget_tokens
    kept_rev = []
    for msg in reversed(history):
        cost = _content_len(msg) // _CHARS_PER_TOKEN
        if kept_rev and cost > budget:
            break
        kept_rev.append(msg)
        budget -= cost
    return list(reversed(kept_rev))


def rebuild_with_summary(head: list, history: list, summary: str,
                         recent_budget_tokens: int) -> list:
    """Rebuild the transcript as head + recent-raw-turns(within budget) + summary.

    `head` is the stable prefix to always keep (e.g. the system message for an
    OpenAI-shaped history; [] for Anthropic where system is out-of-band). The
    summary is appended LAST so the model resumes from it."""
    recent = _recent_within_budget(history, recent_budget_tokens)
    role = "user"  # a neutral role both provider shapes accept as a context message
    summary_msg = {"role": role, "content": _RESUME_PREAMBLE + summary}
    return list(head) + recent + [summary_msg]


def compact_messages(head: list, history: list, *, summarize, recent_budget_tokens: int) -> list:
    """Orchestrate one compaction. `summarize(history) -> str` is injected (the real
    one asks the model for a handoff summary). Fails safe: any summarizer error
    returns the original `head + history` so the conversation is never lost."""
    try:
        summary = summarize(history)
    except Exception:
        return list(head) + list(history)
    if not summary:
        return list(head) + list(history)
    return rebuild_with_summary(head, history, summary, recent_budget_tokens)


def context_window_for(model: str) -> int:
    """Best-effort context window (in tokens) for a model id. An explicit
    ``$KORGEX_CONTEXT_LIMIT`` wins; otherwise a conservative per-family default.
    Used only to decide WHEN to compact, so a rough number is fine."""
    import os
    env = os.environ.get("KORGEX_CONTEXT_LIMIT")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    m = (model or "").lower()
    if "1m" in m or "context-1m" in m:
        return 1_000_000
    if m.startswith("claude") or "anthropic" in m:
        return 200_000
    if m.startswith(("gpt", "o1", "o3", "o4")) or "openai" in m:
        return 128_000
    return 128_000  # conservative default for unknown/local models


SUMMARY_PROMPT = (
    "You are about to run out of context. Write a concise HANDOFF SUMMARY of this "
    "session so another step can continue seamlessly. Include: the goal; what's "
    "been done; key decisions and constraints/preferences; important file paths or "
    "references; and the exact remaining steps. Be specific and terse — no preamble."
)
