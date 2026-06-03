"""Verifiable self-improvement — record the agent's skill self-modifications to the
korg-ledger as first-class, causally-linked events.

The self-learning loop (``skill_review`` + ``skill_curator`` + ``skill_usage``)
rewrites the agent's OWN skill library in the background — learning new skills,
merging duplicates, aging out stale ones. korgex's whole premise is a verifiable
cognition ledger, so those self-modifications must be auditable too, not silent
daemon-thread writes with swallowed errors.

Each function records ONE event via the ledger client's generic ``record_tool_call``,
named ``skill.*`` and chained to the turn that triggered it (``triggered_by``), so
``korgex verify`` / ``trace`` / ``why`` treat a skill change like any other action.
Best-effort by contract: a ledger failure here never propagates — learning is itself
an enhancement and must never break a session.
"""
from __future__ import annotations

from typing import Any

from src.sanitize import redact

# Event names (the ledger's ``tool_name`` field). Namespaced so they're greppable in
# a trace and never collide with a real tool.
LEARNED = "skill.learned"
UPDATED = "skill.updated"
CURATED = "skill.curated"
SWEPT = "skill.swept"
REVIEW_FAILED = "skill.review_failed"

SKILL_EVENTS = (LEARNED, UPDATED, CURATED, SWEPT, REVIEW_FAILED)


def _safe_record(client, tool_name: str, args: Any, result: Any, success: bool,
                 triggered_by: int | None):
    """Record one event, returning the seq_id if the client assigns one synchronously
    (Bridge/Local) or None (async HTTP / failure). Never raises — skill events are an
    audit nicety layered on a best-effort background loop."""
    if client is None:
        return None
    try:
        return client.record_tool_call(
            tool_name=tool_name, args=args, result=result,
            success=success, duration_ms=0, triggered_by=triggered_by)
    except Exception:
        return None


def record_learned(client, *, name: str, action: str, description: str = "",
                   reason: str = "", trust: str = "agent", triggered_by: int | None = None):
    """The agent learned a new skill (``action='create'``) or refined one
    (``'update'``). Chained to the turn's root seq so ``why`` can trace it back."""
    tool = LEARNED if action == "create" else UPDATED
    args = redact({"name": name, "action": action})
    result = redact({"description": description, "reason": reason, "trust": trust})
    return _safe_record(client, tool, args, result, True, triggered_by)


def record_curated(client, *, merged, removed, skipped=None, reason: str = "",
                   triggered_by: int | None = None):
    """A curation pass consolidated near-duplicate learned skills."""
    args = redact({
        "merged": list(merged or []),
        "removed": list(removed or []),
        "skipped": list(skipped or []),
    })
    result = redact({
        "reason": reason,
        "merged_count": len(args.get("merged", [])),
        "removed_count": len(args.get("removed", [])),
    })
    return _safe_record(client, CURATED, args, result, True, triggered_by)


def record_swept(client, *, transitions, triggered_by: int | None = None):
    """Lifecycle aging: ``transitions`` is ``[(name, old_state, new_state), ...]``."""
    rows = [{"name": n, "from": o, "to": w} for (n, o, w) in (transitions or [])]
    args = {"transitions": rows, "count": len(rows)}
    return _safe_record(client, SWEPT, args, {"count": len(rows)}, True, triggered_by)


def record_review_failed(client, *, error, phase: str = "review",
                         triggered_by: int | None = None):
    """A self-improvement pass FAILED — recorded as a ``success=False`` verdict
    instead of being swallowed silently, so the gap is visible in the audit chain."""
    args = {"phase": phase}
    result = redact({"error": str(error)[:500]})
    return _safe_record(client, REVIEW_FAILED, args, result, False, triggered_by)


def skill_log(events) -> list:
    """Filter already-loaded RAW journal events down to skill self-improvement events,
    in seq order, returning compact rows for display + audit. Pure."""
    rows = []
    for ev in events or []:
        tn = ev.get("tool_name")
        if tn not in SKILL_EVENTS:
            continue
        args = ev.get("args") if isinstance(ev.get("args"), dict) else {}
        result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
        rows.append({
            "seq": ev.get("seq_id"),
            "event": tn,
            "name": args.get("name", ""),
            "triggered_by": ev.get("triggered_by"),
            "args": args,
            "result": result,
        })
    return rows


def format_row(row) -> str:
    """One-line human rendering of a ``skill_log`` row — shared by ``/skills log``
    (REPL) and ``korgex skills log`` (CLI) so both read identically."""
    ev, seq = row.get("event", ""), row.get("seq")
    args = row.get("args") or {}
    result = row.get("result") or {}
    tag = {LEARNED: "learned", UPDATED: "updated", CURATED: "curated",
           SWEPT: "swept", REVIEW_FAILED: "FAILED"}.get(ev, ev)
    if ev in (LEARNED, UPDATED):
        why = result.get("reason") or result.get("description") or ""
        s = f"#{seq} {tag}: {row.get('name', '')}"
        return f"{s} — {why}" if why else s
    if ev == CURATED:
        kept = ", ".join(args.get("merged", [])) or "(none)"
        removed = ", ".join(args.get("removed", [])) or "none"
        return f"#{seq} {tag}: kept {kept} · removed {removed}"
    if ev == SWEPT:
        return f"#{seq} {tag}: {args.get('count', 0)} skill(s) aged"
    if ev == REVIEW_FAILED:
        return f"#{seq} {tag}: {args.get('phase', '')} — {(result.get('error') or '')[:80]}"
    return f"#{seq} {ev}"
