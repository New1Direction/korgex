"""Korgex execution-graph DAG — orchestrate tasks with dependencies.

The swarm runs a flat set of subtasks in parallel; this adds the missing piece —
an explicit dependency graph. Nodes declare which other nodes they depend on; the
graph runs them in dependency order, parallelizes independent nodes, **checkpoints**
each completed node to the korg-ledger, and **resumes** a partial run by skipping
nodes already recorded complete. A node that fails blocks its (transitive)
dependents, which are reported as skipped rather than silently dropped.

The graph algebra (cycle detection, topological order, ready-set, failure
propagation, resume) is pure. Execution takes an injected ``executor(node)`` so it
composes with the swarm's real agent runs, a stub in tests, or anything callable.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field


class GraphError(Exception):
    """Raised when a graph is malformed: a cycle or a dependency on an unknown node."""


@dataclass
class Node:
    """A unit of work. `task` is an opaque payload handed to the executor; `deps`
    are the ids of nodes that must complete before this one runs."""
    id: str
    task: object = None
    deps: list = field(default_factory=list)


class ExecGraph:
    def __init__(self, nodes):
        self._nodes = {n.id: n for n in nodes}

    # ── Pure algebra ──────────────────────────────────────────────────────────

    def validate(self) -> None:
        """Raise GraphError on an unknown dependency or a cycle."""
        for n in self._nodes.values():
            for d in n.deps:
                if d not in self._nodes:
                    raise GraphError(f"node '{n.id}' depends on unknown node '{d}'")
        self._topo_or_raise()  # cycle check

    def topological_order(self) -> list:
        """A dependency-respecting order of all node ids. Raises on a cycle."""
        return self._topo_or_raise()

    def _topo_or_raise(self) -> list:
        indeg = {nid: 0 for nid in self._nodes}
        dependents = {nid: [] for nid in self._nodes}
        for n in self._nodes.values():
            for d in n.deps:
                if d in self._nodes:
                    indeg[n.id] += 1
                    dependents[d].append(n.id)
        # Seed with no-dependency roots, sorted for a stable order.
        queue = deque(sorted(nid for nid, dg in indeg.items() if dg == 0))
        order = []
        while queue:
            nid = queue.popleft()
            order.append(nid)
            for m in dependents[nid]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)
        if len(order) != len(self._nodes):
            raise GraphError("graph has a cycle")
        return order

    def ready_nodes(self, completed) -> list:
        """Ids whose dependencies are all in `completed` and aren't done themselves."""
        done = set(completed or set())
        return [nid for nid, n in self._nodes.items()
                if nid not in done and all(d in done for d in n.deps)]

    # ── Execution ───────────────────────────────────────────────────────────────

    def run(self, executor, *, completed=None, on_complete=None, max_parallel: int = 5) -> dict:
        """Run the graph. `executor(node)` does the work and returns a result (or
        raises to fail the node). `completed` (ids) resumes a partial run. `on_complete
        (node, result)` fires per successful node — wire `ledger_checkpoint` here.

        Returns {completed: [ids run this call, in completion order], failed: {id: err},
        skipped: [ids blocked by a failed/unreachable dependency], results: {id: result}}.
        """
        self.validate()
        done = set(completed or set())          # counts as satisfied for deps
        completed_order, failed, results = [], {}, {}

        def ready():
            return [nid for nid, n in self._nodes.items()
                    if nid not in done and nid not in failed
                    and all(d in done for d in n.deps)]

        with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
            wave = ready()
            while wave:
                futures = {pool.submit(executor, self._nodes[nid]): nid for nid in wave}
                for fut in as_completed(futures):
                    nid = futures[fut]
                    try:
                        res = fut.result()
                    except Exception as e:
                        failed[nid] = f"{type(e).__name__}: {e}"
                        continue
                    done.add(nid)
                    completed_order.append(nid)
                    results[nid] = res
                    if on_complete is not None:
                        on_complete(self._nodes[nid], res)
                wave = ready()

        # Anything neither completed nor failed couldn't run — a dependency failed
        # or was itself blocked. Report it rather than dropping it silently.
        skipped = [nid for nid in self._nodes if nid not in done and nid not in failed]
        return {"completed": completed_order, "failed": failed,
                "skipped": skipped, "results": results}


def ledger_checkpoint(journal_client, source: str = "korg:exec_graph"):
    """Return an ``on_complete(node, result)`` that records a checkpoint event per
    completed node to the korg-ledger. Wrapped in ThreadSafeLedger because nodes
    complete on parallel workers. The ``exec_graph.*`` tool name is namespaced, so
    the ledger/trajectory layer treats these as meta events, not conversation."""
    from src.korg_ledger import ThreadSafeLedger

    safe = ThreadSafeLedger(journal_client)

    def on_complete(node, result):
        try:
            safe.record_tool_call(tool_name="exec_graph.node_complete",
                                  args={"node": node.id, "source": source},
                                  result={"ok": True}, success=True, duration_ms=0)
        except Exception:
            pass  # a checkpoint write must never break the run

    return on_complete
