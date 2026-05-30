"""Verifiable training trajectories from korg-ledger@v1 journals.

A korgex run is already recorded to a tamper-evident hash chain. This turns that
chain into a normalized (ShareGPT-style) training trajectory **stamped with its
source's provenance** — so the training data carries proof it was derived from an
unaltered run. Because the source is hash-chained, a poisoned or edited trajectory
is detectable (`provenance.verified == False`): a built-in poisoning defense that
ordinary trajectory loggers can't offer.

Export is **append-only (never-delete)**: trajectories accumulate into a flywheel
of verifiable runs you can train on with confidence about where each one came from.
"""
from __future__ import annotations

import json
import os

from src import ledger_spec as S

# Audit/governance events recorded to the ledger but that are NOT part of the
# training conversation (kept out of trajectories). Any namespaced tool name
# (containing ".", e.g. hook.PreToolUse / guardrail.block / checkpoint.pre_edit)
# is also treated as meta.
_META_TOOLS = {"edit_policy", "test_gate", "memory_reconcile"}


def _is_conversational(ev: dict) -> bool:
    tn = ev.get("tool_name", "")
    if "." in tn or tn.startswith("checkpoint"):
        return False
    return tn not in _META_TOOLS


def _turn(ev: dict) -> dict:
    tn = ev.get("tool_name", "")
    if tn == "user_prompt":
        return {"from": "human", "value": (ev.get("args") or {}).get("prompt", "")}
    if tn == "llm_inference":
        return {"from": "gpt", "value": (ev.get("result") or {}).get("text", "")}
    return {"from": "tool", "value": json.dumps(
        {"tool": tn, "args": ev.get("args"), "result": ev.get("result")}, sort_keys=True)}


def to_trajectory(events: list) -> dict:
    """Convert a korg-ledger@v1 journal into a provenance-stamped trajectory."""
    conversations = [_turn(e) for e in events if _is_conversational(e)]
    verified = not S.verify_chain(events)
    return {
        "conversations": conversations,
        "provenance": {
            "spec": S.SPEC_VERSION,
            "source_agent": events[0].get("source_agent") if events else None,
            "events": len(events),
            "verified": verified,
            "tip_hash": events[-1].get("entry_hash") if events else None,
        },
    }


def export_trajectory(journal_path: str, out_path: str | None = None) -> dict:
    """Read a journal, build its trajectory, and APPEND it to `out_path` (never
    overwrites — exports accumulate). Returns a summary."""
    with open(journal_path) as f:
        events = [json.loads(ln) for ln in f if ln.strip()]
    traj = to_trajectory(events)
    out_path = out_path or (os.path.splitext(journal_path)[0] + ".trajectory.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "a") as f:  # append-only: the never-delete flywheel
        f.write(json.dumps(traj) + "\n")
    return {
        "out_path": out_path,
        "turns": len(traj["conversations"]),
        "events": traj["provenance"]["events"],
        "verified": traj["provenance"]["verified"],
    }
