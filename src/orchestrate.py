"""
orchestrate.py — a first-class, ledger-native fan-out/DAG primitive.

NOT a new engine. It composes the pieces korgex already proves:

  - ExecGraph (src/exec_graph.py): cycle detection, topological order, ready-set
    waves, parallel independent nodes, failure-propagation (a failed node's
    transitive dependents land in skipped[], never run against a broken
    precondition), and resume-by-completed.
  - ThreadSafeLedger (src/korg_ledger.py): the documented concurrency contract —
    nodes complete on parallel workers, so every write goes through one lock and
    seq_ids stay unique + monotonic + backward-pointing.
  - ledger_checkpoint (src/exec_graph.py): a per-completed-node checkpoint event.
  - an injected ``runner(node, parent_seq)`` that does the real work — in
    production a closure on KorgexAgent that calls _run_subagent(...,
    parent_seq=root), so EVERY node's root chains under ONE orchestrate root and
    each node inherits tool-filtering + the typed subagent.result node + the
    one-level-nesting bound FOR FREE.

The result is the moat: a whole orchestration is ONE connected, replayable,
tamper-evident causal DAG. The FAILURE topology itself (node_failed /
node_skipped) is committed to the chain — mainstream frameworks log that to
stderr and lose it; korgex makes it verifiable.

Out of scope (stated honestly): cross-process ledger locking, background-writer
durability across a crash, streaming verify, semantic causality validation.
This is single-process fan-out only.
"""
from __future__ import annotations

import logging

from src.exec_graph import ExecGraph, Node, ledger_checkpoint
from src.korg_ledger import ThreadSafeLedger

logger = logging.getLogger(__name__)


def record_seed(ledger, seed, triggered_by=None) -> int:
    """Record an immutable spec-seed — the agreed 'what we're building' — as a
    hash-chained ledger event, and return its seq.

    `seed` is a goal string or a dict {goal, constraints, acceptance_criteria}.
    A run chains UNDER the returned seq, so `korgex why`/`trace` walk any later
    edit back to the spec it was meant to satisfy and `korgex verify` proves the
    spec wasn't altered after the fact (immutable by construction: it's a normal
    hash-chained event — tampering breaks verify_chain). This is the one borrowed
    idea (from spec-first tools) that strengthens korgex's verifiable thesis."""
    spec = seed if isinstance(seed, dict) else {"goal": str(seed)}
    norm = {
        "goal": str(spec.get("goal", "")),
        "constraints": list(spec.get("constraints") or []),
        "acceptance_criteria": list(spec.get("acceptance_criteria")
                                    or spec.get("acceptance") or []),
    }
    return ledger.record_tool_call(
        tool_name="spec.seed", args=norm, result={"sealed": True},
        success=True, duration_ms=0, triggered_by=triggered_by,
    )


def run_orchestration(spec, runner, ledger, parent_seq) -> dict:
    """Run a user-defined DAG of subagents as one verifiable causal subtree.

    `spec` = {"nodes": [{id, prompt, subagent_type, deps:[ids], model?}, ...],
    "max_parallel": int}. `runner(node, parent_seq)` executes one node and returns
    a subagent-shaped result ({success, result, root_seq, ...}); it raises to fail
    the node. `ledger` is the run's ledger; `parent_seq` chains the orchestrate
    root under the spawning turn (None at the top level).

    Returns {root_seq, completed[], failed{}, skipped[], results{id:{...}}}.
    """
    nodes_spec = list((spec or {}).get("nodes") or [])
    max_parallel = int((spec or {}).get("max_parallel", 5) or 5)

    # The ONE provable root of the whole swarm subtree. Recorded BEFORE wrapping
    # so a single root exists even if the wrap is a no-op (already thread-safe).
    base = ledger
    safe = base if isinstance(base, ThreadSafeLedger) else ThreadSafeLedger(base)
    # Optional spec-seed: an immutable 'what we agreed to build' the whole run
    # anchors under (goal + constraints + acceptance criteria). When present, the
    # orchestrate root chains under the seed; otherwise under the spawning turn.
    seed = (spec or {}).get("seed")
    seed_seq = record_seed(safe, seed, triggered_by=parent_seq) if seed is not None else None
    # Chain the orchestrate root under the spawning turn (or the seed) so the whole
    # subtree is CONNECTED to the parent conversation's DAG (not a disconnected island).
    root_seq = safe.record_user_prompt(
        "[orchestrate] " + ", ".join(str(n.get("id")) for n in nodes_spec),
        triggered_by=(seed_seq if seed_seq is not None else parent_seq))

    graph = ExecGraph([
        Node(n["id"], task=n, deps=list(n.get("deps") or []))
        for n in nodes_spec
    ])

    # Each node's root chains under the ONE orchestrate root → one connected DAG.
    def executor(node):
        return runner(node, root_seq)

    run_out = graph.run(
        executor,
        on_complete=ledger_checkpoint(safe, source="korg:orchestrate"),
        max_parallel=max_parallel,
    )

    completed = run_out["completed"]
    failed = run_out["failed"]
    skipped = run_out["skipped"]
    raw_results = run_out["results"]

    # Normalize per-node results into a stable {id: {success, result, child_root_seq}}
    results: dict = {}
    child_root_seqs = []
    for nid, res in raw_results.items():
        res = res or {}
        child_root = res.get("root_seq")
        if child_root is not None:
            child_root_seqs.append(child_root)
        results[nid] = {
            "success": bool(res.get("success", True)),
            "result": res.get("result"),
            "child_root_seq": child_root,
        }

    # Typed aggregation node naming all child root seqs — the audit/recall layer
    # can traverse the orchestrate subtree without parsing tool-result blobs.
    # Wrapped in try/except: audit must NEVER break the run (mirrors _run_subagent).
    try:
        safe.record_tool_call(
            tool_name="orchestrate.result",
            args={"nodes": [str(n.get("id")) for n in nodes_spec]},
            result={"completed": completed, "failed": list(failed.keys()),
                    "skipped": skipped, "child_root_seqs": child_root_seqs},
            success=not failed and not skipped,
            duration_ms=0, triggered_by=root_seq,
        )
    except Exception:
        logger.warning("[orchestrate] aggregation node write failed", exc_info=True)

    # The FAILURE topology is itself part of the verifiable DAG: a typed event per
    # failed / skipped node, chained under the orchestrate root. Each wrapped so a
    # write error never breaks the run.
    for nid, err in failed.items():
        try:
            safe.record_tool_call(
                tool_name="orchestrate.node_failed",
                args={"node": nid}, result={"error": str(err)},
                success=False, duration_ms=0, triggered_by=root_seq,
            )
        except Exception:
            logger.warning("[orchestrate] node_failed write failed", exc_info=True)
    for nid in skipped:
        try:
            safe.record_tool_call(
                tool_name="orchestrate.node_skipped",
                args={"node": nid},
                result={"reason": "a dependency failed or was unreachable"},
                success=False, duration_ms=0, triggered_by=root_seq,
            )
        except Exception:
            logger.warning("[orchestrate] node_skipped write failed", exc_info=True)

    return {"root_seq": root_seq, "seed_seq": seed_seq, "completed": completed,
            "failed": failed, "skipped": skipped, "results": results}
