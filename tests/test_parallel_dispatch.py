"""
Parallel Agent-call dispatch (Slice 1).

The serial dispatch loop runs every tool call one after another, INCLUDING the
Agent tool — so an LLM batch emitting Agent x3 blocks on each child (1-60s) in
turn. This splits OFF contiguous PURE-Agent batches and fans them out through
korgantic.parallel over a ThreadSafeLedger, leaving the serial loop (and every
FS-touching gate) byte-for-byte intact for the common case.

The load-bearing invariants proven here:
  - two Agent calls in one batch actually run concurrently (barrier),
  - results are appended in ORIGINAL call order (LLM history must match the
    assistant turn) even when children finish out of order,
  - every child root chains under the spawning llm_seq (siblings, backward
    edges) → verify_dag stays [],
  - the ThreadSafeLedger is installed for the batch and not double-wrapped,
  - a crashing sibling does NOT abort the survivors and is NEVER reported as
    success,
  - single-Agent and mixed batches keep the untouched serial path,
  - the worker cap (KORGEX_PARALLEL_AGENTS) is honored.
"""

import json
import os
import sys
import threading
import time
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.agent import KorgexAgent  # noqa: E402
from src.korg_ledger import ThreadSafeLedger, verify_dag  # noqa: E402


# ── harness (mirrors tests/test_subagents.py) ─────────────────────────────

class _FakeLedger:
    def __init__(self):
        self.events = []
        self._seq = 0
        self._lock = threading.Lock()

    def _next(self):
        # NOT atomic on purpose — the race point a ThreadSafeLedger must close.
        self._seq += 1
        return self._seq

    def record_user_prompt(self, prompt, triggered_by=None):
        self.events.append({"kind": "user_prompt", "triggered_by": triggered_by})
        return self._next()

    def record_llm_call(self, **kw):
        self.events.append({"kind": "llm", **kw})
        return self._next()

    def record_tool_call(self, **kw):
        self.events.append({"kind": "tool", **kw})
        return self._next()


class _MemLedger:
    """In-memory ledger with a deliberately non-atomic seq counter (the race
    point). Records seq_id + triggered_by so verify_dag can run over it."""

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


def _openai_text(text):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
    )


def _openai_agent_calls(calls):
    """An assistant turn carrying N Agent tool_calls. `calls` is a list of
    (call_id, prompt, subagent_type)."""
    tool_calls = [
        SimpleNamespace(
            id=cid,
            function=SimpleNamespace(
                name="Agent",
                arguments=json.dumps({"prompt": prompt, "subagent_type": st,
                                      "description": "do a thing"}),
            ),
        )
        for (cid, prompt, st) in calls
    ]
    msg = SimpleNamespace(content=None, tool_calls=tool_calls)
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=msg)])


def _openai_mixed_agent_and_write(agent_id, write_id, path):
    msg = SimpleNamespace(content=None, tool_calls=[
        SimpleNamespace(id=agent_id, function=SimpleNamespace(
            name="Agent",
            arguments=json.dumps({"prompt": "explore", "subagent_type": "explore",
                                  "description": "do a thing"}))),
        SimpleNamespace(id=write_id, function=SimpleNamespace(
            name="Write",
            arguments=json.dumps({"file_path": path, "content": "hi"}))),
    ])
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=msg)])


class _ScriptedAgent(KorgexAgent):
    def __init__(self, responses, ledger=None, **kw):
        kw.setdefault("model", "gpt-4o")
        kw.setdefault("interactive", False)
        super().__init__(**kw)
        self._responses = list(responses)
        self.ledger = ledger if ledger is not None else _FakeLedger()

    def _get_client(self):
        return object()

    def _call(self, client, messages, tools, output_schema=None,
              system_prompt=None, system_volatile=None):
        return self._responses.pop(0)


# ── Step 1: two Agent calls in one batch run concurrently ──────────────────

def test_two_agent_calls_in_one_batch_run_concurrently(monkeypatch):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "4")
    parent = _ScriptedAgent([
        _openai_agent_calls([("call_1", "explore A", "explore"),
                             ("call_2", "explore B", "explore")]),
        _openai_text("parent done"),
    ])

    # Barrier of 2: if dispatch is serial the second child never arrives, the
    # first child times out waiting, and BOTH fail. Concurrent → both pass.
    barrier = threading.Barrier(2, timeout=3)
    roots = []
    roots_lock = threading.Lock()

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            barrier.wait()  # blocks unless a sibling is in-flight concurrently
            child_root = self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)
            with roots_lock:
                roots.append((parent_seq, child_root))
            return {"success": True, "result": f"did {prompt}", "iterations": 1,
                    "root_seq": child_root}

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    result = parent.run_task("delegate two explorations")

    assert result["success"] is True
    assert len(roots) == 2  # both children ran (barrier was satisfied → concurrent)
    # both chained under the SAME spawning llm_seq (siblings of one LLM round)
    parent_seqs = {ps for (ps, _cr) in roots}
    assert len(parent_seqs) == 1
    assert None not in parent_seqs


# ── Step 3: parallel Agent results preserve call order ─────────────────────

def test_parallel_agent_results_preserve_call_order(monkeypatch):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "4")
    parent = _ScriptedAgent([
        _openai_agent_calls([("call_1", "slow one", "explore"),
                             ("call_2", "fast one", "explore")]),
        _openai_text("parent done"),
    ])

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            # call_1 finishes LAST (sleeps longer) — completion order is reversed.
            time.sleep(0.25 if "slow" in prompt else 0.02)
            child_root = self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)
            return {"success": True, "result": f"did {prompt}", "iterations": 1,
                    "root_seq": child_root}

    captured_messages = {}
    real_call = parent._call

    def _capture(client, messages, tools, **kw):
        # Snapshot the message list the LATEST LLM round-trip sees; by the final
        # "parent done" round it carries the post-batch tool-result turns.
        captured_messages["msgs"] = list(messages)
        return real_call(client, messages, tools, **kw)

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    parent._call = _capture
    parent.run_task("delegate slow then fast")

    # Find the two tool_result turns; call_1 (slow) MUST precede call_2 (fast)
    # even though call_2 completed first — order tracks the assistant turn.
    msgs = captured_messages["msgs"]
    ids_in_order = [m.get("tool_call_id") for m in msgs if m.get("role") == "tool"]
    assert ids_in_order == ["call_1", "call_2"], ids_in_order

    # both subagent.result aggregation events exist on the ledger
    agg = [e for e in parent.ledger.events
           if e["kind"] == "tool" and e.get("tool_name") == "subagent.result"]
    assert len(agg) == 2


# ── Step 4: child roots chain under the spawning llm_seq (valid DAG) ───────

def test_parallel_batch_builds_valid_dag_under_thread_safe_ledger(monkeypatch):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "4")
    inner = _MemLedger()
    parent = _ScriptedAgent([
        _openai_agent_calls([("call_1", "explore A", "explore"),
                             ("call_2", "explore B", "explore"),
                             ("call_3", "explore C", "explore")]),
        _openai_text("parent done"),
    ], ledger=ThreadSafeLedger(inner))

    llm_seqs = []

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            llm_seqs.append(parent_seq)
            child_root = self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)
            self.ledger.record_llm_call(triggered_by=child_root)
            return {"success": True, "result": f"did {prompt}", "iterations": 1,
                    "root_seq": child_root}

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    parent.run_task("delegate three explorations")

    # every child saw the SAME spawning llm_seq (siblings, backward edges)
    assert len(set(llm_seqs)) == 1 and None not in llm_seqs
    # the three child roots all point back at that one llm_seq (the parent's own
    # root user_prompt is also on this shared ledger, so filter to the children)
    child_roots = [e for e in inner.events
                   if e["kind"] == "user_prompt" and e["triggered_by"] == llm_seqs[0]]
    assert len(child_roots) == 3
    # the whole concurrently-written DAG is well-formed (unique monotonic seqs,
    # every triggered_by points strictly backward, nothing dropped)
    assert verify_dag(inner.events) == []


# ── Step 5: ThreadSafeLedger installed for the batch, no double-wrap ───────

def test_thread_safe_ledger_installed_during_batch_and_restored(monkeypatch):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "4")
    fake = _FakeLedger()
    parent = _ScriptedAgent([
        _openai_agent_calls([("call_1", "a", "explore"), ("call_2", "b", "explore")]),
        _openai_text("parent done"),
    ], ledger=fake)

    seen_types = []

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            seen_types.append(type(parent.ledger).__name__)
            return {"success": True, "result": "ok", "iterations": 1,
                    "root_seq": self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)}

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    parent.run_task("delegate")

    # DURING the batch the agent's ledger is the ThreadSafeLedger wrapper …
    assert seen_types and all(t == "ThreadSafeLedger" for t in seen_types)
    # … and AFTER it is restored to the original _FakeLedger.
    assert parent.ledger is fake


def test_thread_safe_ledger_not_double_wrapped(monkeypatch):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "4")
    inner = _FakeLedger()
    pre_wrapped = ThreadSafeLedger(inner)
    parent = _ScriptedAgent([
        _openai_agent_calls([("call_1", "a", "explore"), ("call_2", "b", "explore")]),
        _openai_text("parent done"),
    ], ledger=pre_wrapped)

    seen = {}

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            # The wrapper handed down is the SAME instance (no re-wrap): its inner
            # client is the original _FakeLedger, not another ThreadSafeLedger.
            seen["is_same_wrapper"] = self.ledger is pre_wrapped
            seen["inner_is_fake"] = getattr(self.ledger, "_inner", None) is inner
            return {"success": True, "result": "ok", "iterations": 1,
                    "root_seq": self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)}

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    parent.run_task("delegate")

    assert seen["is_same_wrapper"] is True
    assert seen["inner_is_fake"] is True
    assert parent.ledger is pre_wrapped  # restored to the pre-wrapped ledger


# ── Step 6: one crashing subagent does not abort its siblings ──────────────

def test_crashing_subagent_does_not_abort_siblings(monkeypatch):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "4")
    parent = _ScriptedAgent([
        _openai_agent_calls([("call_1", "boom", "explore"),
                             ("call_2", "fine", "explore")]),
        _openai_text("parent done"),
    ])

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            if "boom" in prompt:
                raise RuntimeError("child exploded")
            child_root = self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)
            return {"success": True, "result": "survivor ok", "iterations": 1,
                    "root_seq": child_root}

    captured_messages = {}
    real_call = parent._call

    def _capture(client, messages, tools, **kw):
        captured_messages["msgs"] = list(messages)
        return real_call(client, messages, tools, **kw)

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    parent._call = _capture
    result = parent.run_task("delegate boom and fine")

    # (a) the surviving child completed; (c) the parent run still succeeds.
    assert result["success"] is True
    assert result["result"] == "parent done"

    # (b) the crashed child's tool_result reports success=False, NOT a None
    # masquerading as success.
    def _payload(m):
        return json.loads(m["content"])

    by_id = {m["tool_call_id"]: _payload(m) for m in captured_messages["msgs"]
             if m.get("role") == "tool"}
    assert by_id["call_1"]["success"] is False
    assert "crashed" in by_id["call_1"]["result"]
    assert by_id["call_2"]["success"] is True
    assert by_id["call_2"]["result"] == "survivor ok"


# ── Step 7: serial path unchanged for single-Agent and mixed batches ───────

def test_single_agent_call_stays_serial(monkeypatch):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "4")
    parent = _ScriptedAgent([
        _openai_agent_calls([("call_1", "just one", "explore")]),
        _openai_text("parent done"),
    ])

    called = {"batch": False}
    parent._dispatch_agent_batch = lambda *a, **k: called.__setitem__("batch", True) or {}

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            return {"success": True, "result": "ok", "iterations": 1,
                    "root_seq": self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)}

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    parent.run_task("delegate one")

    assert called["batch"] is False  # a single Agent call never fans out


def test_mixed_agent_and_write_batch_stays_serial(monkeypatch, tmp_path):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "4")
    target = str(tmp_path / "out.txt")
    parent = _ScriptedAgent([
        _openai_mixed_agent_and_write("call_agent", "call_write", target),
        _openai_text("parent done"),
    ])

    called = {"batch": False}
    parent._dispatch_agent_batch = lambda *a, **k: called.__setitem__("batch", True) or {}

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            return {"success": True, "result": "ok", "iterations": 1,
                    "root_seq": self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)}

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    parent.run_task("explore then write")

    assert called["batch"] is False              # mixed batch never fans out
    assert os.path.exists(target)                # the Write still ran serially
    with open(target) as f:
        assert f.read() == "hi"


# ── Step 8: worker cap honored ─────────────────────────────────────────────

def test_worker_cap_is_honored(monkeypatch):
    monkeypatch.setenv("KORGEX_PARALLEL_AGENTS", "2")
    parent = _ScriptedAgent([
        _openai_agent_calls([(f"call_{i}", f"explore {i}", "explore") for i in range(5)]),
        _openai_text("parent done"),
    ])

    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            with lock:
                live["now"] += 1
                live["max"] = max(live["max"], live["now"])
            time.sleep(0.05)
            with lock:
                live["now"] -= 1
            return {"success": True, "result": "ok", "iterations": 1,
                    "root_seq": self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)}

    parent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    parent.run_task("delegate five")

    assert live["max"] <= 2  # never more than KORGEX_PARALLEL_AGENTS in flight
