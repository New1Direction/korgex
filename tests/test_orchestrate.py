"""
Orchestrate — a first-class, ledger-native fan-out/DAG tool (Slice 2).

NOT a new engine: it composes ExecGraph (cycle detection, topo order, ready-set
waves, failure propagation, resume) + _run_subagent (typed subagent.result node,
one-level nesting, tool filtering) + ThreadSafeLedger (concurrent-write safety).
A whole orchestration is ONE connected, replayable, tamper-evident causal DAG:
every node's root chains under one orchestrate root via triggered_by, and the
FAILURE topology (node_failed / node_skipped) is itself committed to the chain.

Proven here:
  - one orchestrate root; every node root traces back to it; verify_dag == [],
  - a failed node skips its dependents AND records typed verifiable events,
  - the Orchestrate tool dispatches, self-verifies its subtree, and respects
    one-level nesting (subagents get neither Agent nor Orchestrate),
  - the programmatic run_orchestration_task wraps the ledger thread-safe,
  - an end-to-end run on a REAL journal verifies green (and a tampered copy
    fails verify_chain).
"""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.korg_ledger import ThreadSafeLedger, verify_dag  # noqa: E402


# ── in-memory ledger with a non-atomic seq counter (the race point) ────────

class _MemLedger:
    def __init__(self):
        self.events = []
        self._seq = 0

    def _append(self, kind, triggered_by, tool_name=None, args=None, result=None):
        self._seq += 1
        ev = {"seq_id": self._seq, "kind": kind, "triggered_by": triggered_by}
        if tool_name is not None:
            ev["tool_name"] = tool_name
        if args is not None:
            ev["args"] = args
        if result is not None:
            ev["result"] = result
        self.events.append(ev)
        return self._seq

    def record_user_prompt(self, prompt, triggered_by=None):
        return self._append("user_prompt", triggered_by)

    def record_llm_call(self, **kw):
        return self._append("llm", kw.get("triggered_by"))

    def record_tool_call(self, **kw):
        return self._append("tool", kw.get("triggered_by"),
                            tool_name=kw.get("tool_name"), args=kw.get("args"),
                            result=kw.get("result"))


def _stub_runner(ledger):
    """A node runner that records each node's root on the (shared) ledger under
    the orchestrate root (passed as parent_seq) and returns the subagent-shaped
    result. Mirrors the production _run_subagent closure without running any LLM.
    run_orchestration owns root_seq and threads it in as parent_seq."""
    seen = []

    def runner(node, parent_seq):
        step = node.task
        child_root = ledger.record_user_prompt(step["prompt"], triggered_by=parent_seq)
        seen.append(node.id)
        return {"success": True, "result": f"did {node.id}", "iterations": 1,
                "root_seq": child_root}

    return runner, seen


# ── Step 9: run_orchestration builds ONE connected, verifiable DAG ─────────

def test_run_orchestration_builds_one_connected_verifiable_dag():
    from src.orchestrate import run_orchestration

    inner = _MemLedger()
    runner, seen = _stub_runner(inner)
    spec = {"nodes": [
        {"id": "a", "prompt": "do a", "subagent_type": "explore", "deps": []},
        {"id": "b", "prompt": "do b", "subagent_type": "explore", "deps": ["a"]},
        {"id": "c", "prompt": "do c", "subagent_type": "explore", "deps": ["a"]},
    ], "max_parallel": 5}

    out = run_orchestration(spec, runner, inner, parent_seq=None)

    # exactly ONE orchestrate root user_prompt was recorded.
    roots = [e for e in inner.events if e["kind"] == "user_prompt"]
    orchestrate_roots = [e for e in roots if e["triggered_by"] is None]
    assert len(orchestrate_roots) == 1
    root_seq = orchestrate_roots[0]["seq_id"]
    assert out["root_seq"] == root_seq

    # b and c both ran after a (dependency order) …
    assert seen.index("a") < seen.index("b")
    assert seen.index("a") < seen.index("c")

    # … and every node root traces back to the orchestrate root (one connected
    # subtree): node roots chain under root_seq.
    node_roots = [e for e in roots if e["triggered_by"] == root_seq]
    assert len(node_roots) == 3

    # the whole concurrently-written DAG is well-formed.
    assert verify_dag(inner.events) == []
    assert set(out["completed"]) == {"a", "b", "c"}
    assert out["failed"] == {}
    assert out["skipped"] == []


# ── Step 10: failed node skips dependents AND records typed events ─────────

def test_failed_node_skips_dependents_and_records_typed_events():
    from src.orchestrate import run_orchestration

    inner = _MemLedger()

    def runner(node, parent_seq):
        if node.id == "a":
            raise RuntimeError("node a exploded")
        child_root = inner.record_user_prompt(node.task["prompt"], triggered_by=parent_seq)
        return {"success": True, "result": f"did {node.id}", "iterations": 1,
                "root_seq": child_root}

    spec = {"nodes": [
        {"id": "a", "prompt": "do a", "subagent_type": "code", "deps": []},
        {"id": "b", "prompt": "do b", "subagent_type": "code", "deps": ["a"]},
        {"id": "c", "prompt": "do c", "subagent_type": "code", "deps": ["b"]},
    ]}

    out = run_orchestration(spec, runner, inner, parent_seq=None)

    # ExecGraph semantics: a failed → b, c (transitive dependents) skipped.
    assert "a" in out["failed"]
    assert set(out["skipped"]) == {"b", "c"}
    assert out["completed"] == []

    # the FAILURE topology is committed to the chain as typed events.
    failed_ev = [e for e in inner.events
                 if e.get("tool_name") == "orchestrate.node_failed"]
    skipped_ev = [e for e in inner.events
                  if e.get("tool_name") == "orchestrate.node_skipped"]
    assert {e["args"]["node"] for e in failed_ev} == {"a"}
    assert {e["args"]["node"] for e in skipped_ev} == {"b", "c"}

    # each is chained under the one orchestrate root.
    root = [e for e in inner.events
            if e["kind"] == "user_prompt" and e["triggered_by"] is None][0]
    for e in failed_ev + skipped_ev:
        assert e["triggered_by"] == root["seq_id"]

    # the DAG stays valid even WITH the failure topology recorded.
    assert verify_dag(inner.events) == []


# ── Step 12: programmatic run_orchestration_task wraps ledger thread-safe ──

def _scripted_agent(ledger):
    """A KorgexAgent whose LLM never runs (no _call invoked by orchestration)
    and whose subagents are stubbed via subagent_factory."""
    from src.agent import KorgexAgent

    class _A(KorgexAgent):
        def __init__(self, **kw):
            kw.setdefault("model", "gpt-4o")
            kw.setdefault("interactive", False)
            super().__init__(**kw)
            self.ledger = ledger

        def _get_client(self):
            return object()

    agent = _A()

    class _Child:
        def __init__(self, led):
            self.ledger = led

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            child_root = self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)
            return {"success": True, "result": f"did {prompt}", "iterations": 1,
                    "root_seq": child_root}

    agent.subagent_factory = lambda **kw: _Child(kw["ledger"])
    return agent


def test_run_orchestration_task_wraps_thread_safe_and_one_root():
    captured = {}

    class _SpyMem(_MemLedger):
        def record_user_prompt(self, prompt, triggered_by=None):
            # capture the agent's live ledger TYPE at the first root write
            captured.setdefault("ledger_type_during", type(agent.ledger).__name__)
            return super().record_user_prompt(prompt, triggered_by=triggered_by)

    inner = _SpyMem()
    agent = _scripted_agent(inner)
    prev = agent.ledger

    spec = {"nodes": [
        {"id": "a", "prompt": "do a", "subagent_type": "explore", "deps": []},
        {"id": "b", "prompt": "do b", "subagent_type": "explore", "deps": ["a"]},
    ], "max_parallel": 5}

    out = agent.run_orchestration_task(spec)

    # ThreadSafeLedger was installed for the run …
    assert captured["ledger_type_during"] == "ThreadSafeLedger"
    # … and restored afterward.
    assert agent.ledger is prev

    # exactly ONE orchestrate root recorded.
    roots = [e for e in inner.events if e["kind"] == "user_prompt"]
    orchestrate_roots = [e for e in roots if e["triggered_by"] is None]
    assert len(orchestrate_roots) == 1
    assert out["root_seq"] == orchestrate_roots[0]["seq_id"]

    # the documented return shape.
    assert set(out) >= {"completed", "failed", "skipped", "results", "root_seq", "dag_verified"}
    assert set(out["completed"]) == {"a", "b"}
    assert out["dag_verified"] is True
    assert verify_dag(inner.events) == []


def test_run_orchestration_task_does_not_double_wrap_thread_safe():
    inner = _MemLedger()
    agent = _scripted_agent(ThreadSafeLedger(inner))
    pre_wrapped = agent.ledger

    spec = {"nodes": [{"id": "a", "prompt": "do a", "subagent_type": "explore", "deps": []}]}
    agent.run_orchestration_task(spec)

    # the inner client is the original _MemLedger, not a nested ThreadSafeLedger.
    assert agent.ledger is pre_wrapped
    assert getattr(pre_wrapped, "_inner", None) is inner
    assert verify_dag(inner.events) == []


# ── Step 13: end-to-end verifiable run on a REAL journal ───────────────────

def test_orchestration_on_real_journal_verifies_green(tmp_path):
    from src.korg_ledger import LocalJournalClient, verify_journal_file

    journal = str(tmp_path / "orchestrate.journal")
    client = LocalJournalClient(journal_path=journal, source_agent="korg:orchestrate")
    runner, _seen = _stub_runner(client)

    spec = {"nodes": [
        {"id": "a", "prompt": "do a", "subagent_type": "explore", "deps": []},
        {"id": "b", "prompt": "do b", "subagent_type": "explore", "deps": ["a"]},
        {"id": "c", "prompt": "do c", "subagent_type": "explore", "deps": ["a"]},
    ], "max_parallel": 5}

    from src.orchestrate import run_orchestration
    out = run_orchestration(spec, runner, client, parent_seq=None)
    assert set(out["completed"]) == {"a", "b", "c"}

    # the WHOLE swarm is provably intact on disk: verify_dag AND verify_chain
    # both pass over the real JSONL journal.
    assert verify_journal_file(journal) == []


def test_tampered_journal_fails_verify_chain(tmp_path, monkeypatch):
    from src.korg_ledger import LocalJournalClient, verify_chain

    monkeypatch.setenv("KORG_LEDGER_HMAC_KEY", "test-secret-key")
    journal = str(tmp_path / "orchestrate_hmac.journal")
    client = LocalJournalClient(journal_path=journal, source_agent="korg:orchestrate")
    runner, _seen = _stub_runner(client)

    from src.orchestrate import run_orchestration
    spec = {"nodes": [
        {"id": "a", "prompt": "do a", "subagent_type": "explore", "deps": []},
        {"id": "b", "prompt": "do b", "subagent_type": "explore", "deps": ["a"]},
    ]}
    run_orchestration(spec, runner, client, parent_seq=None)

    # the untouched, HMAC-keyed chain verifies green.
    key = b"test-secret-key"
    events = [json.loads(line) for line in open(journal) if line.strip()]
    assert verify_chain(events, key=key) == []

    # hand-tamper a recorded event's bytes → verify_chain detects it.
    for ev in events:
        if ev.get("tool_name") == "user_prompt":
            ev["args"] = {"prompt": "FORGED"}
            break
    assert verify_chain(events, key=key)  # non-empty → tamper detected


# ── Hardening regressions (verifier-found bugs, fixed) ─────────────────────

def test_orchestrate_tool_path_wraps_thread_safe_and_chains_root():
    """The Orchestrate TOOL path (_run_orchestration, what _dispatch_call calls)
    must install ThreadSafeLedger for the concurrent NODE subagent writes — not
    just orchestrate's own bookkeeping — and chain its root under the spawning
    turn. (Bug: the tool path left self.ledger unwrapped, so node subagents — which
    read self.ledger — raced seq_ids; and the root was orphaned with triggered_by=None.)"""
    captured = {}

    class _SpyMem(_MemLedger):
        def record_user_prompt(self, prompt, triggered_by=None):
            if str(prompt).startswith("[orchestrate]"):
                captured["ledger_type_during"] = type(agent.ledger).__name__
            return super().record_user_prompt(prompt, triggered_by=triggered_by)

    inner = _SpyMem()
    agent = _scripted_agent(inner)
    parent_seq = inner.record_user_prompt("parent turn")  # the spawning turn

    spec = {"nodes": [
        {"id": "a", "prompt": "a", "subagent_type": "explore", "deps": []},
        {"id": "b", "prompt": "b", "subagent_type": "explore", "deps": []},
        {"id": "c", "prompt": "c", "subagent_type": "explore", "deps": []},
    ], "max_parallel": 4}
    out = agent._run_orchestration(spec, parent_seq)

    # the concurrent node writes ran under the lock …
    assert captured["ledger_type_during"] == "ThreadSafeLedger"
    assert agent.ledger is inner                       # … and it was restored.
    # no seq collision and a sound, CONNECTED DAG (root chains under the parent).
    seqs = [e["seq_id"] for e in inner.events]
    assert len(seqs) == len(set(seqs))
    assert verify_dag(inner.events) == []
    root_ev = [e for e in inner.events if e["seq_id"] == out["root_seq"]][0]
    assert root_ev["triggered_by"] == parent_seq       # not orphaned (was None)


def test_orchestrate_dag_verified_is_none_when_events_unreadable():
    """A backend whose events can't be read in-process (e.g. the bridge) must
    report dag_verified=None ('could not verify here'), NOT False — a valid run
    must never be mislabeled invalid."""
    class _OpaqueLedger:                # no .events, not a LocalJournalClient
        def __init__(self):
            self._seq = 0

        def _next(self):
            self._seq += 1
            return self._seq

        def record_user_prompt(self, prompt, triggered_by=None):
            return self._next()

        def record_llm_call(self, **kw):
            return self._next()

        def record_tool_call(self, **kw):
            return self._next()

    agent = _scripted_agent(_OpaqueLedger())
    spec = {"nodes": [{"id": "a", "prompt": "a", "subagent_type": "explore", "deps": []}]}
    out = agent._run_orchestration(spec, None)
    assert out["dag_verified"] is None                 # not False


def test_dispatch_hard_blocks_delegation_for_restricted_subagent():
    """One-level nesting is HARD-enforced at dispatch, not merely by omission from
    the advertised tool list: a subagent (tools_filter excluding Agent/Orchestrate)
    that nonetheless emits one — hallucination / injection — is blocked, never spawns."""
    from src.agent import subagent_tools

    agent = _scripted_agent(_MemLedger())
    explore = subagent_tools("explore")
    assert "Agent" not in explore and "Orchestrate" not in explore   # the soft layer
    for name in ("Agent", "Orchestrate"):
        res = agent._dispatch_call({"name": name, "args": {}, "id": "x"},
                                   parent_seq=1, tools_filter=explore)
        assert isinstance(res, dict) and "not permitted" in res.get("error", "")
