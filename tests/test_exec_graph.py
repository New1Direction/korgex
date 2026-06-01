"""Tests for the execution-graph DAG (src/exec_graph.py).

Turns the flat swarm into an explicit task DAG: nodes with dependencies that run
in dependency order, parallelize where independent, checkpoint each completed node
to the ledger, and RESUME by skipping already-completed nodes. The graph algebra
(cycle detection, topo order, ready-set, failure propagation, resume) is pure and
fully tested; execution uses an injected executor so no real agent runs.
"""
import threading

import pytest

from src.exec_graph import ExecGraph, GraphError, Node, ledger_checkpoint


def order_recorder():
    """An executor that records completion order (thread-safe) and returns the id."""
    seen, lock = [], threading.Lock()

    def executor(node):
        with lock:
            seen.append(node.id)
        return f"ran:{node.id}"
    return executor, seen


# ── Pure graph algebra ────────────────────────────────────────────────────────

def test_topological_order_respects_dependencies():
    g = ExecGraph([Node("a"), Node("b", deps=["a"]), Node("c", deps=["b"])])
    order = g.topological_order()
    assert order.index("a") < order.index("b") < order.index("c")


def test_cycle_is_detected():
    g = ExecGraph([Node("a", deps=["b"]), Node("b", deps=["a"])])
    with pytest.raises(GraphError):
        g.validate()


def test_missing_dependency_is_detected():
    g = ExecGraph([Node("a", deps=["ghost"])])
    with pytest.raises(GraphError):
        g.validate()


def test_ready_nodes_are_those_with_satisfied_deps():
    g = ExecGraph([Node("a"), Node("b", deps=["a"]), Node("c", deps=["a"])])
    assert g.ready_nodes(completed=set()) == ["a"]
    assert sorted(g.ready_nodes(completed={"a"})) == ["b", "c"]
    assert g.ready_nodes(completed={"a", "b", "c"}) == []


# ── Execution ─────────────────────────────────────────────────────────────────

def test_run_executes_in_dependency_order():
    executor, seen = order_recorder()
    g = ExecGraph([Node("a"), Node("b", deps=["a"]), Node("c", deps=["b"])])
    out = g.run(executor)
    assert seen.index("a") < seen.index("b") < seen.index("c")
    assert out["completed"] == ["a", "b", "c"]


def test_run_returns_results_keyed_by_node():
    executor, _ = order_recorder()
    g = ExecGraph([Node("a"), Node("b", deps=["a"])])
    out = g.run(executor)
    assert out["results"] == {"a": "ran:a", "b": "ran:b"}


def test_independent_nodes_run_concurrently():
    # Two independent nodes must be in-flight at the same time, or the barrier
    # (which needs 2 parties) times out and the nodes fail — a real concurrency
    # proof, not a timing guess.
    barrier = threading.Barrier(2, timeout=3)

    def executor(node):
        barrier.wait()
        return node.id

    g = ExecGraph([Node("a"), Node("b")])
    out = g.run(executor, max_parallel=2)
    assert set(out["completed"]) == {"a", "b"}
    assert out["failed"] == {}


def test_failed_node_skips_its_dependents_but_not_independents():
    def executor(node):
        if node.id == "a":
            raise RuntimeError("boom")
        return node.id

    g = ExecGraph([
        Node("a"),
        Node("b", deps=["a"]),   # depends on the failing node → skipped
        Node("c"),               # independent → still runs
    ])
    out = g.run(executor)
    assert "a" in out["failed"]
    assert "b" in out["skipped"]
    assert "c" in out["completed"]


def test_transitive_dependents_of_failure_are_all_skipped():
    def executor(node):
        if node.id == "a":
            raise RuntimeError("boom")
        return node.id

    g = ExecGraph([Node("a"), Node("b", deps=["a"]), Node("c", deps=["b"])])
    out = g.run(executor)
    assert "a" in out["failed"]
    assert set(out["skipped"]) == {"b", "c"}


# ── Resume / checkpoints ───────────────────────────────────────────────────────

def test_resume_skips_already_completed_nodes():
    executor, seen = order_recorder()
    g = ExecGraph([Node("a"), Node("b", deps=["a"]), Node("c", deps=["b"])])
    out = g.run(executor, completed={"a", "b"})
    assert seen == ["c"]              # a and b were not re-run
    assert "c" in out["completed"]


def test_on_complete_called_per_successful_node():
    executor, _ = order_recorder()
    checkpoints = []
    g = ExecGraph([Node("a"), Node("b", deps=["a"])])
    g.run(executor, on_complete=lambda node, result: checkpoints.append(node.id))
    assert sorted(checkpoints) == ["a", "b"]


def test_ledger_checkpoint_appends_one_event_per_node(tmp_path):
    from src.korg_ledger import LocalJournalClient

    journal = str(tmp_path / "exec.journal")
    client = LocalJournalClient(journal_path=journal, source_agent="korg:exec_graph")
    executor, _ = order_recorder()

    g = ExecGraph([Node("a"), Node("b", deps=["a"])])
    g.run(executor, on_complete=ledger_checkpoint(client))

    # Two nodes completed → two checkpoint events appended to the chain.
    import json
    with open(journal) as f:
        events = [json.loads(line) for line in f if line.strip()]
    node_events = [e for e in events if e.get("tool_name") == "exec_graph.node_complete"]
    assert len(node_events) == 2
