"""
Real-subagent tests (roadmap P1, the biggest bet).

Replaces the fictional `swarm.py` (which piped prompts to a bare python3 with
no LLM) with real nested KorgexAgent runs, and makes the Agent tool actually
work. The differentiator: a subagent's root chains into the PARENT's causal
seq via triggered_by, so a multi-agent run is ONE connected DAG in the shared
ledger (rewindable per-branch), and subagents cannot recursively spawn agents.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.agent import KorgexAgent, subagent_tools  # noqa: E402
from src import korg_ledger as L  # noqa: E402


# ── 1. tool filtering ─────────────────────────────────────────────────────

def test_get_provider_tools_respects_filter():
    a = KorgexAgent(model="claude-sonnet-4-6", interactive=False)
    tools = a._get_provider_tools(tools_filter={"Read", "Grep"})
    assert {t["name"] for t in tools} == {"Read", "Grep"}


def test_subagent_tools_readonly_excludes_mutators():
    ro = set(subagent_tools("explore"))
    assert "Read" in ro
    assert "Write" not in ro and "Bash" not in ro


def test_subagent_tools_code_excludes_agent_to_prevent_recursion():
    code = set(subagent_tools("code"))
    assert "Agent" not in code  # subagents can't spawn subagents
    assert "Write" in code      # but otherwise full access


# ── 2. ledger: root chains under a parent seq ─────────────────────────────

def test_record_user_prompt_threads_triggered_by(monkeypatch):
    c = L.KorgLedgerClient(base_url="http://x")
    c._available = True
    captured = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"seq_id": 7}

    def fake_post(url, json, timeout):
        captured.clear()
        captured.update(json)
        return Resp()

    monkeypatch.setattr(L.requests, "post", fake_post)

    seq = c.record_user_prompt("hi", triggered_by=5)
    assert seq == 7
    assert captured["triggered_by"] == 5


def test_record_user_prompt_omits_triggered_by_when_root(monkeypatch):
    c = L.KorgLedgerClient(base_url="http://x")
    c._available = True
    captured = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"seq_id": 1}

    monkeypatch.setattr(L.requests, "post",
                        lambda url, json, timeout: (captured.update(json) or Resp()))
    c.record_user_prompt("hi")  # no parent
    assert "triggered_by" not in captured


# ── 3. agent integration: run_task re-entrancy + Agent tool ───────────────

class _FakeLedger:
    def __init__(self):
        self.events = []
        self._seq = 0

    def _next(self):
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
        return None


def _openai_text(text):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
    )


def _openai_agent_call(call_id, prompt, subagent_type):
    msg = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(
                name="Agent",
                arguments=json.dumps({"prompt": prompt, "subagent_type": subagent_type,
                                      "description": "do a thing"}),
            ),
        )],
    )
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=msg)])


class _ScriptedAgent(KorgexAgent):
    def __init__(self, responses, **kw):
        kw.setdefault("model", "gpt-4o")
        kw.setdefault("interactive", False)
        super().__init__(**kw)
        self._responses = list(responses)
        self.ledger = _FakeLedger()

    def _get_client(self):
        return object()

    def _call(self, client, messages, tools, output_schema=None):
        return self._responses.pop(0)


def test_run_task_chains_under_parent_and_returns_root_seq():
    agent = _ScriptedAgent([_openai_text("child done")])
    result = agent.run_task("a child task", parent_seq=42)
    roots = [e for e in agent.ledger.events if e["kind"] == "user_prompt"]
    assert roots[0]["triggered_by"] == 42      # chained into the parent run
    assert result["root_seq"] == 1             # the seq the ledger assigned the root


def test_agent_tool_spawns_real_subagent_into_shared_ledger():
    parent = _ScriptedAgent([
        _openai_agent_call("call_1", "explore the codebase", "explore"),
        _openai_text("parent done"),
    ])

    seen = {}

    class _Child:
        def __init__(self, ledger):
            self.ledger = ledger

        def run_task(self, prompt, parent_seq=None, tools_filter=None, output_schema=None):
            seen["prompt"] = prompt
            seen["parent_seq"] = parent_seq
            seen["tools_filter"] = list(tools_filter) if tools_filter else None
            seen["shared_ledger"] = self.ledger is parent.ledger
            child_root = self.ledger.record_user_prompt(prompt, triggered_by=parent_seq)
            return {"success": True, "result": "explored 12 files", "iterations": 1,
                    "root_seq": child_root}

    def factory(**kw):
        return _Child(kw["ledger"])

    parent.subagent_factory = factory
    result = parent.run_task("delegate exploration")

    assert result["success"] is True
    # the subagent was driven with the explore tool subset, chained to a parent seq
    assert seen["parent_seq"] is not None
    assert "Agent" not in (seen["tools_filter"] or [])
    assert "Write" not in (seen["tools_filter"] or [])  # explore is read-only
    assert seen["shared_ledger"] is True
    # the parent recorded an Agent tool event carrying the subagent's outcome
    agent_events = [e for e in parent.ledger.events
                    if e["kind"] == "tool" and e.get("tool_name") == "Agent"]
    assert agent_events
    assert "explored 12 files" in json.dumps(agent_events[0]["result"])


# ── 4. swarm runs REAL agents, not python3 ────────────────────────────────

def test_subagent_worker_runs_real_agent_via_factory():
    from src.swarm import SubagentWorker, SubTask

    seen = {}

    class FakeAgent:
        def run_task(self, prompt, **kw):
            seen["prompt"] = prompt
            return {"success": True, "result": "wrote 3 tests", "iterations": 2}

    task = SubTask("test", "write tests for the auth module", "/repo")
    worker = SubagentWorker(task, agent_factory=lambda t: FakeAgent())
    res = worker.run()

    assert res.success is True
    assert "wrote 3 tests" in res.output
    assert "auth module" in seen["prompt"]   # the task actually reached the agent


def test_swarm_run_concurrent_aggregates_real_agents():
    from src.swarm import AgentSwarm, SubTask

    class FakeAgent:
        def run_task(self, prompt, **kw):
            return {"success": True, "result": "done", "iterations": 1}

    swarm = AgentSwarm(agent_factory=lambda t: FakeAgent())
    results = swarm.run_concurrent([
        SubTask("test", "a", "/r"),
        SubTask("security", "b", "/r"),
    ])
    assert len(results) == 2
    assert all(r.success for r in results)
