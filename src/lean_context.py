"""Lean context from the verifiable ledger — retrieve, don't carry.

Instead of feeding a model the entire history every turn, pull the few past ledger
events relevant to the current step and render them as a compact, provenance-stamped
block. Because the events are hash-chained, the retrieved memory is trustworthy — the
model isn't handed a summary to believe, it's handed verifiable facts with their seq
ids. Short prompts → cheaper, faster inference → a smaller (even self-hosted) model
can drive the same loop.

Documentation-first: each line says WHAT happened, in plain terms; the ``#seq`` is the
handle to check it (``korgex why`` walks its cause, ``korgex verify`` proves the chain
it lives in is unedited). Retrieval reuses ``recall.search`` — this module is only the
selection-into-a-budget + rendering layer on top.
"""
from __future__ import annotations

from src import recall

# A whole-prompt context line over ~this many chars gets truncated, so one noisy
# event can't crowd out the rest of the budget.
_MAX_PROMPT = 100
_MAX_TARGET = 80


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~4 chars/token). Approximate by design —
    it bounds a context budget, it is not billing."""
    return (len(text) + 3) // 4 if text else 0


def _seq(event: dict):
    return event.get("seq_id", event.get("seq"))


def summarize_event(event: dict) -> str:
    """One documentation-first line for an event, or "" for events that are noise in an
    action view (the thinking rounds). Format: ``#<seq> <what happened>``."""
    kind = event.get("tool_name") or event.get("event_type") or ""
    if kind == "llm_inference":
        return ""
    seq = _seq(event)
    tag = f"#{seq}" if seq is not None else "#?"
    args = event.get("args") or {}

    if kind in ("user_prompt", "user_message"):
        text = (args.get("prompt") or event.get("prompt") or args.get("text") or "").strip().replace("\n", " ")
        if len(text) > _MAX_PROMPT:
            text = text[:_MAX_PROMPT - 1] + "…"
        return f'{tag} asked: "{text}"'

    target = str(args.get("file_path") or args.get("path") or args.get("notebook_path")
                 or args.get("command") or args.get("cmd") or "").replace("\n", " ")
    if len(target) > _MAX_TARGET:
        target = target[:_MAX_TARGET - 1] + "…"
    mark = " ✓" if event.get("success", True) else " ✗"
    return f"{tag} {kind} {target}{mark}".rstrip()


def build_lean_context(events, query, *, budget_tokens: int = 1500, top_n: int = 20,
                       mode: str = "auto", causal=False) -> dict:
    """Retrieve the events relevant to `query`, render them as a compact block within
    `budget_tokens`, chronological for a coherent narrative. Always keeps at least the
    single most relevant line (so a tiny budget still yields something). Returns
    ``{text, refs, events_used, tokens_est}`` — `refs` are the cited seq ids, the
    provenance handles back into the verifiable chain.

    `causal` expands the matches along the ledger's `triggered_by` DAG
    (``recall.expand_causal``): ``True``/``"both"`` pulls causes and effects, ``"causes"``
    only the prompt that triggered a matched action (no sibling leak — best for per-step
    context), ``"effects"`` only the actions a matched prompt triggered. The budget still
    caps the rendered lines. Off by default so plain text-relevance retrieval is
    unchanged."""
    hits = recall.search(events or [], query, top_n=top_n, mode=mode)
    seed_events = [h["event"] for h in hits]
    if causal:
        direction = "both" if causal is True else str(causal)
        seed_events = recall.expand_causal(events or [], seed_events, depth=1,
                                           direction=direction, max_total=max(top_n * 3, 30))
    chosen = sorted(seed_events, key=lambda e: _seq(e) or 0)

    lines: list[str] = []
    refs: list[int] = []
    tokens = 0
    for ev in chosen:
        line = summarize_event(ev)
        if not line:
            continue
        cost = estimate_tokens(line)
        if lines and tokens + cost > budget_tokens:   # always allow the first line through
            break
        lines.append(line)
        tokens += cost
        sid = _seq(ev)
        if isinstance(sid, int):
            refs.append(sid)

    return {"text": "\n".join(lines), "refs": refs,
            "events_used": len(lines), "tokens_est": tokens}


def unresolved_refs(refs, events) -> list:
    """The refs (seq ids) in a lean-context block that DON'T resolve to a real event in
    `events`. Empty means every cited memory traces to the chain — then `korgex verify`
    proves that chain is unedited, so the lean context can't be quietly fabricated."""
    have = {_seq(e) for e in (events or [])}
    return [r for r in (refs or []) if r not in have]
