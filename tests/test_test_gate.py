"""
Test-gate tests (Gate B — the single highest-leverage reliability mechanism).

Ground truth for "is this edit good" is test execution, not the model's
self-assessment. After a run that mutated files, the configured test command
runs and a RED result forces success=False (the edit is not accepted) and lands
a verdict on the ledger. A read-only run never triggers the gate.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import test_gate as TG  # noqa: E402
from src.agent import KorgexAgent  # noqa: E402


# ── 1. run_test_gate (real subprocess) ────────────────────────────────────

def test_run_test_gate_pass(tmp_path):
    r = TG.run_test_gate("true", cwd=str(tmp_path))
    assert r["passed"] is True and r["exit_code"] == 0


def test_run_test_gate_fail_captures_output(tmp_path):
    r = TG.run_test_gate("echo boom >&2; exit 1", cwd=str(tmp_path))
    assert r["passed"] is False and r["exit_code"] == 1
    assert "boom" in r["output"]


# ── 2. load_test_gate config ──────────────────────────────────────────────

def test_load_test_gate_missing(tmp_path):
    assert TG.load_test_gate(str(tmp_path)) is None


def test_load_test_gate_reads_settings(tmp_path):
    cfg = tmp_path / ".korgex" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"testGate": {"command": "pytest -q"}}))
    gate = TG.load_test_gate(str(tmp_path))
    assert gate["command"] == "pytest -q"


# ── 3. agent integration: red gate blocks acceptance ──────────────────────

class _FakeLedger:
    def __init__(self):
        self.events = []

    def record_user_prompt(self, prompt, triggered_by=None):
        self.events.append({"kind": "user_prompt"}); return 1

    def record_llm_call(self, **kw):
        self.events.append({"kind": "llm", **kw}); return 2

    def record_tool_call(self, **kw):
        self.events.append({"kind": "tool", **kw}); return None


def _openai_write(call_id, file_path, content):
    msg = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
        id=call_id, function=SimpleNamespace(
            name="Write", arguments=json.dumps({"file_path": file_path, "content": content})))])
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=msg)])


def _openai_text(text):
    return SimpleNamespace(usage=None,
                           choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))])


class _ScriptedAgent(KorgexAgent):
    def __init__(self, responses, **kw):
        kw.setdefault("model", "gpt-4o")
        kw.setdefault("interactive", False)
        super().__init__(**kw)
        self._responses = list(responses)
        self.ledger = _FakeLedger()

    def _get_client(self):
        return object()

    def _call(self, client, messages, tools, output_schema=None, system_prompt=None, system_volatile=None):
        return self._responses.pop(0)


def test_red_test_gate_forces_failure_after_an_edit(tmp_path):
    agent = _ScriptedAgent(
        [_openai_write("c1", "f.py", "x = 1"), _openai_text("done")],
        repo_root=str(tmp_path),
    )
    agent.test_gate = {"command": "exit 1"}   # tests are red
    result = agent.run_task("edit and finish")

    assert result["success"] is False
    assert result["test_gate"]["passed"] is False
    verdicts = [e for e in agent.ledger.events
                if e.get("kind") == "tool" and e.get("tool_name") == "test_gate"]
    assert verdicts and verdicts[0]["result"]["verdict"] == "FAILED"


def test_green_test_gate_keeps_success(tmp_path):
    agent = _ScriptedAgent(
        [_openai_write("c1", "f.py", "x = 1"), _openai_text("done")],
        repo_root=str(tmp_path),
    )
    agent.test_gate = {"command": "true"}
    result = agent.run_task("edit and finish")
    assert result["success"] is True
    assert result["test_gate"]["passed"] is True


def test_gate_skipped_when_no_edits(tmp_path):
    # read-only run (no mutating tool) → the gate must NOT run, even if it'd fail
    agent = _ScriptedAgent([_openai_text("just answering")], repo_root=str(tmp_path))
    agent.test_gate = {"command": "exit 1"}
    result = agent.run_task("just answer, no edits")
    assert result["success"] is True
    assert "test_gate" not in result
    assert not any(e.get("tool_name") == "test_gate" for e in agent.ledger.events)
