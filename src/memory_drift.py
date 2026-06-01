"""
memory_drift.py — make stale memory an exact, auditable signal (roadmap P2 #5).

The one genuine differentiator vs other agents: they punt the
trust-hierarchy problem (a remembered fact silently rots; you discover it when
the agent acts on it). korgex anchors each memory to the sha256 of what it was
derived from, so drift is a content-hash *fact* — and the decision to keep,
refresh, or discard a drifted memory is written to the ledger, where it rides
the tamper-evident hash-chain (see korg_ledger.verify_chain). The reconcile
event IS the audit answer: "we knew this memory had drifted, and here's what we
decided, provably un-altered since."

Sources:
  - a file path (default)         → baseline = sha256(current file bytes)
  - "fact:<text>"                 → baseline = sha256(the literal text)
A memory with no source is *unanchored* — drift is unknown, not false. Surfacing
those is itself valuable: they're the ones you can't trust.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

VALID_DECISIONS = ("keep", "refresh", "discard")
_FACT_PREFIX = "fact:"
_FILE_PREFIX = "file:"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_baseline(source: str, repo_root: str | None = None) -> str | None:
    """Current sha256 of a memory's source, or None if it can't be resolved.

    A "fact:<text>" source hashes the literal text; anything else is treated as
    a file path (a leading "file:" is stripped) resolved against repo_root.
    """
    if not source:
        return None
    if source.startswith(_FACT_PREFIX):
        return _sha(source[len(_FACT_PREFIX):].encode("utf-8"))
    path = source[len(_FILE_PREFIX):] if source.startswith(_FILE_PREFIX) else source
    target = Path(path)
    if not target.is_absolute():
        target = Path(repo_root or os.getcwd()) / path
    try:
        return _sha(target.read_bytes())
    except OSError:
        return None


def check_drift(source: str, baseline_sha: str | None,
                repo_root: str | None = None) -> dict:
    """Compare a memory's source against its recorded baseline.

    status ∈ {fresh, drifted, missing, unanchored}. drifted is True/False, or
    None when there's no baseline to compare (status=unanchored) — unknown, not
    false, because an unverifiable memory is exactly the dangerous case.
    """
    if not baseline_sha:
        return {"source": source, "baseline_sha": None,
                "current_sha": compute_baseline(source, repo_root),
                "drifted": None, "status": "unanchored",
                "reason": "no baseline recorded — cannot verify"}

    current = compute_baseline(source, repo_root)
    if current is None:
        return {"source": source, "baseline_sha": baseline_sha,
                "current_sha": None, "drifted": True, "status": "missing",
                "reason": "source no longer resolvable (gone since recorded)"}

    drifted = current != baseline_sha
    return {"source": source, "baseline_sha": baseline_sha,
            "current_sha": current, "drifted": drifted,
            "status": "drifted" if drifted else "fresh",
            "reason": "source changed since recorded" if drifted
                      else "matches recorded baseline"}


def scan(memories: list, repo_root: str | None = None) -> dict:
    """Partition memories by drift status. Returns a report with per-status name
    lists, the full verdicts, and has_drift (any drifted OR missing)."""
    report: dict = {"fresh": [], "drifted": [], "missing": [],
                    "unanchored": [], "verdicts": []}
    for mem in memories:
        name = mem.get("name")
        source = mem.get("source")
        if not source:
            report["unanchored"].append(name)
            report["verdicts"].append({"name": name, "status": "unanchored",
                                       "drifted": None,
                                       "reason": "memory declares no source"})
            continue
        v = check_drift(source, mem.get("source_sha"), repo_root)
        v["name"] = name
        report["verdicts"].append(v)
        report[v["status"]].append(name)
    report["has_drift"] = bool(report["drifted"] or report["missing"])
    return report


def recall_block(memories: list, repo_root: str | None = None,
                 record_event=None, triggered_by=None) -> dict:
    """Verify memories at recall time and build a TRUSTED prompt block (idea #5).

    Anchored memories are checked against their source baselines: fresh ones are
    injected, drifted/missing ones are WITHHELD and a `memory_reconcile` decision
    (decision="flag") is recorded via `record_event` (chained off `triggered_by`).
    Unanchored memories (no source) are injected as-is — they can't drift.

    `record_event(tool_name, args, result, success, triggered_by) -> seq` is the
    ledger sink. Returns {block, injected:[names], flagged:[names], report}.
    The differentiator: every injected fact is verified-current, and every stale
    one is on the record — auditable memory, not just memory.
    """
    report = scan(memories, repo_root)
    flagged = set(report["drifted"]) | set(report["missing"])
    verdict_by_name = {v.get("name"): v for v in report["verdicts"]}

    injected, lines = [], []
    last_seq = triggered_by
    for mem in memories:
        name = mem.get("name")
        if name in flagged:
            if record_event is not None:
                v = verdict_by_name.get(name, {})
                last_seq = record_event(
                    "memory_reconcile",
                    {"memory_name": name, "decision": "flag", "source": mem.get("source")},
                    {"status": v.get("status"), "reason": v.get("reason")},
                    False, last_seq)
            continue
        anchored = bool(mem.get("source"))
        tag = "✓ verified-current" if anchored else "unverified"
        desc = (mem.get("description") or "").strip()
        body = (mem.get("body") or "").strip().splitlines()
        snippet = body[0][:240] if body else ""
        lines.append(f"- **{name}** ({tag}) — {desc}\n  {snippet}")
        injected.append(name)

    block = ""
    if lines:
        block = ("# Recalled memory\n\n"
                 "Facts recalled this turn. Anchored memories were checked against their "
                 "source baselines; stale ones were withheld and flagged on the ledger — "
                 "trust these over your priors.\n\n" + "\n".join(lines))
    return {"block": block, "injected": injected, "flagged": list(flagged), "report": report}


def record_reconcile(ledger, memory_name: str, decision: str,
                     baseline_sha: str | None = None,
                     current_sha: str | None = None,
                     triggered_by: int | None = None) -> int:
    """Write the reconcile decision to the ledger as a `memory_reconcile` event.

    Flows through the same record_tool_call path as every other event, so the
    decision is hash-chained and tamper-evident. Returns the assigned seq_id.
    """
    if decision not in VALID_DECISIONS:
        raise ValueError(
            f"decision must be one of {VALID_DECISIONS}, got {decision!r}")
    return ledger.record_tool_call(
        "memory_reconcile",
        {"memory_name": memory_name, "decision": decision,
         "baseline_sha": baseline_sha, "current_sha": current_sha},
        {"ok": True}, True, 0, triggered_by=triggered_by)


def reconcile(memory: dict, decision: str, ledger, repo_root: str | None = None,
              triggered_by: int | None = None) -> dict:
    """Check a memory for drift and record the keep/refresh/discard decision.

    Returns {memory, decision, seq_id, verdict} plus an action hint:
      - refresh → new_source_sha (caller rewrites the memory; memory is
        immutable, so that's a delete + recreate with the new baseline);
      - discard → delete=True.
    'keep' acknowledges the drift on the record without changing the memory.
    """
    verdict = check_drift(memory.get("source"), memory.get("source_sha"), repo_root)
    seq = record_reconcile(ledger, memory.get("name"), decision,
                           baseline_sha=memory.get("source_sha"),
                           current_sha=verdict.get("current_sha"),
                           triggered_by=triggered_by)
    out = {"memory": memory.get("name"), "decision": decision,
           "seq_id": seq, "verdict": verdict}
    if decision == "refresh":
        out["new_source_sha"] = verdict.get("current_sha")
    elif decision == "discard":
        out["delete"] = True
    return out
