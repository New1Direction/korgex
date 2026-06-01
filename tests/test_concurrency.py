"""
Concurrency contract + branching-rewind invariant (gates a real parallel()).

korgantic's parallel() fans out subagents that all write ONE ledger. Without a
contract, concurrent seq assignment / triggered_by can race and corrupt the
causal DAG — the exact property the ledger sells. This proves:

1. ThreadSafeLedger serializes concurrent writes → a well-formed DAG (unique
   monotonic seqs, every edge points strictly backward, nothing dropped).
2. parallel() is a barrier with per-thunk error isolation (raise → None).
3. rewind-by-truncation preserves causal integrity — BECAUSE every triggered_by
   points strictly backward, truncating at seq N can never orphan a survivor.
   (The Rust bridge performs the actual rewind; this proves the invariant that
   makes branched rewind sound.)
"""

import os
import sys
import threading

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.korg_ledger import ThreadSafeLedger, verify_dag, rewind_events  # noqa: E402
from src import korgantic as K  # noqa: E402


class _MemLedger:
    """In-memory ledger with a deliberately non-atomic seq counter (the race point)."""

    def __init__(self):
        self.events = []
        self._seq = 0

    def _append(self, kind, triggered_by):
        self._seq += 1
        self.events.append({"seq_id": self._seq, "kind": kind, "triggered_by": triggered_by})
        return self._seq

    def record_user_prompt(self, prompt, triggered_by=None):
        return self._append("user_prompt", triggered_by)

    def record_llm_call(self, **kw):
        return self._append("llm", kw.get("triggered_by"))

    def record_tool_call(self, **kw):
        return self._append("tool", kw.get("triggered_by"))


# ── 1. verify_dag invariant checker ───────────────────────────────────────

def test_verify_dag_accepts_wellformed_tree():
    events = [{"seq_id": 1, "triggered_by": None},
              {"seq_id": 2, "triggered_by": 1},
              {"seq_id": 3, "triggered_by": 1}]
    assert verify_dag(events) == []


def test_verify_dag_flags_forward_edge():
    # an event that claims to be caused by a LATER event is impossible
    events = [{"seq_id": 1, "triggered_by": 2}, {"seq_id": 2, "triggered_by": None}]
    assert verify_dag(events)


def test_verify_dag_flags_missing_parent():
    events = [{"seq_id": 1, "triggered_by": None}, {"seq_id": 2, "triggered_by": 99}]
    assert verify_dag(events)


def test_verify_dag_flags_duplicate_seq():
    events = [{"seq_id": 1, "triggered_by": None}, {"seq_id": 1, "triggered_by": None}]
    assert verify_dag(events)


# ── 2. ThreadSafeLedger under concurrency ─────────────────────────────────

def test_thread_safe_ledger_keeps_dag_consistent_under_concurrency():
    inner = _MemLedger()
    led = ThreadSafeLedger(inner)

    def worker(i):
        root = led.record_user_prompt(f"task {i}")
        led.record_llm_call(triggered_by=root)
        led.record_tool_call(tool_name="X", args={}, result={}, success=True,
                             duration_ms=0, triggered_by=root)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(inner.events) == 75                                   # nothing dropped
    assert sorted(e["seq_id"] for e in inner.events) == list(range(1, 76))  # unique, no gaps
    assert verify_dag(inner.events) == []                            # every edge valid + backward


# ── 3. parallel() barrier + error isolation ───────────────────────────────

def test_parallel_isolates_errors_and_preserves_order():
    def boom():
        raise ValueError("kaboom")

    out = K.parallel([lambda: 1, boom, lambda: 3])
    assert out == [1, None, 3]


def test_parallel_runs_all_thunks():
    seen = set()
    lock = threading.Lock()

    def mk(i):
        def f():
            with lock:
                seen.add(i)
            return i
        return f

    out = K.parallel([mk(i) for i in range(12)])
    assert sorted(out) == list(range(12))
    assert len(seen) == 12


def test_parallel_empty_is_noop():
    assert K.parallel([]) == []


# ── 4. branching-rewind invariant ─────────────────────────────────────────

def test_rewind_truncation_preserves_dag_integrity():
    # a branched DAG: seq 2 and 3 both fork off the root
    events = [
        {"seq_id": 1, "triggered_by": None},
        {"seq_id": 2, "triggered_by": 1},
        {"seq_id": 3, "triggered_by": 1},
        {"seq_id": 4, "triggered_by": 2},
        {"seq_id": 5, "triggered_by": 3},
    ]
    assert verify_dag(events) == []

    survivors = rewind_events(events, 3)
    assert [e["seq_id"] for e in survivors] == [1, 2, 3]
    # truncation never orphaned a survivor — the backward-edge invariant guarantees it
    assert verify_dag(survivors) == []
    # everything causally downstream of the cut is gone
    assert all(e["seq_id"] <= 3 for e in survivors)


# ── 5. concurrent run_task must not share system-prompt state ──────────────

class _FakeLedger:
    def record_user_prompt(self, prompt, triggered_by=None):
        return 1

    def record_llm_call(self, **kw):
        return 2

    def record_tool_call(self, **kw):
        return None


def test_concurrent_run_task_each_sees_its_own_system_prompt():
    """The production korgantic runner is self.run_task on ONE agent instance,
    invoked concurrently by the sweep + verification fan-out. Each call must use
    the system prompt assembled on ITS thread — never another thread's."""
    from types import SimpleNamespace
    from src.agent import KorgexAgent

    captured = {}

    class _A(KorgexAgent):
        def __init__(self, **kw):
            kw.setdefault("model", "gpt-4o")
            kw.setdefault("interactive", False)
            super().__init__(**kw)
            self.ledger = _FakeLedger()

        def _assemble_system_prompt(self):
            # distinct per thread → a clobber would make a thread see another's prompt
            return f"SP::{threading.current_thread().name}"

        def _get_client(self):
            return object()

        def _call(self, client, messages, tools, output_schema=None, system_prompt=None, system_volatile=None):
            captured[threading.current_thread().name] = system_prompt
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))],
            )

    agent = _A()

    def worker():
        agent.run_task("a task")

    threads = [threading.Thread(target=worker, name=f"w{i}") for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(captured) == 10
    for name, sp in captured.items():
        assert sp == f"SP::{name}", f"{name} saw a clobbered prompt: {sp}"
