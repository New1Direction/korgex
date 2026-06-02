"""
Hook-system tests — deterministic, ledger-native extensibility (roadmap P1).

The differentiator isn't "korgex has hooks" (table-stakes) — it's that every
PreToolUse allow/deny is recorded as a verdict event on the causal ledger, so
governance over tool calls is rewindable and auditable. These tests cover the
pure dispatcher (load/match/run) and the agent integration (a blocked tool
never executes AND a BLOCKED verdict lands on the ledger).
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import hooks as H  # noqa: E402
from src.agent import KorgexAgent  # noqa: E402


# ── 1. load_hooks ─────────────────────────────────────────────────────────

def test_load_hooks_missing_returns_empty(tmp_path):
    assert H.load_hooks(str(tmp_path)) == {}


def test_load_hooks_reads_settings(tmp_path):
    cfg = tmp_path / ".korgex" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "command": "true"}]}}))
    hooks = H.load_hooks(str(tmp_path))
    assert "PreToolUse" in hooks
    assert hooks["PreToolUse"][0]["matcher"] == "Bash"


def test_load_hooks_malformed_returns_empty(tmp_path):
    cfg = tmp_path / ".korgex" / "settings.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{ not json")
    assert H.load_hooks(str(tmp_path)) == {}


# ── 2. match_hooks ────────────────────────────────────────────────────────

def test_match_by_exact_tool_name():
    defs = [{"matcher": "Bash", "command": "x"}, {"matcher": "Read", "command": "y"}]
    matched = H.match_hooks(defs, "Bash")
    assert len(matched) == 1 and matched[0]["command"] == "x"


def test_match_regex_alternation():
    defs = [{"matcher": "Bash|Write", "command": "x"}]
    assert H.match_hooks(defs, "Write")
    assert not H.match_hooks(defs, "Read")


def test_absent_matcher_matches_everything():
    defs = [{"command": "x"}]  # no matcher
    assert H.match_hooks(defs, "Anything")


# ── 3. run_hook (real subprocess) ─────────────────────────────────────────

def test_run_hook_exit_zero_allows():
    r = H.run_hook("exit 0", {"tool_name": "Bash"})
    assert r["decision"] == "allow"


def test_run_hook_exit_two_blocks():
    r = H.run_hook("exit 2", {"tool_name": "Bash"})
    assert r["decision"] == "block"


def test_run_hook_block_json_on_stdout_carries_reason():
    cmd = "python3 -c \"import json; print(json.dumps({'decision':'block','reason':'protected path'}))\""
    r = H.run_hook(cmd, {"tool_name": "Edit"})
    assert r["decision"] == "block"
    assert "protected path" in r["reason"]


def test_run_hook_receives_payload_on_stdin():
    # Block only when the piped tool_name is Bash — proves stdin delivery.
    cmd = "python3 -c \"import json,sys; d=json.load(sys.stdin); sys.exit(2 if d['tool_name']=='Bash' else 0)\""
    assert H.run_hook(cmd, {"tool_name": "Bash"})["decision"] == "block"
    assert H.run_hook(cmd, {"tool_name": "Read"})["decision"] == "allow"


# ── 4. run_event aggregation ──────────────────────────────────────────────

def test_run_event_first_block_wins_and_carries_policy_hash():
    hooks = {"PreToolUse": [{"matcher": "Bash", "command": "exit 2"}]}
    blocked = H.run_event("PreToolUse", "Bash", {"tool_name": "Bash"}, hooks)
    assert blocked["decision"] == "block"
    assert blocked["policy_hash"]  # attributable to the rule that fired
    assert blocked["ran"]

    allowed = H.run_event("PreToolUse", "Read", {"tool_name": "Read"}, hooks)
    assert allowed["decision"] == "allow"
    assert allowed["ran"] == []  # nothing matched Read


# ── 5. Agent integration: blocked tool never runs + BLOCKED verdict ledgered ─

class _FakeLedger:
    def __init__(self):
        self.events = []

    def record_user_prompt(self, prompt, triggered_by=None):
        self.events.append({"kind": "user_prompt"})
        return 1

    def record_llm_call(self, **kw):
        self.events.append({"kind": "llm", **kw})
        return 2

    def record_tool_call(self, **kw):
        self.events.append({"kind": "tool", **kw})
        return None


def _openai_bash_call(call_id, command):
    msg = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name="Bash", arguments=json.dumps({"command": command})),
        )],
    )
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=msg)])


def _openai_text(text):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
    )


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


def test_pre_tool_use_block_prevents_execution_and_records_verdict(tmp_path):
    sentinel = tmp_path / "SHOULD_NOT_EXIST"
    # The model tries to `touch` the sentinel via Bash; a PreToolUse hook blocks Bash.
    agent = _ScriptedAgent([
        _openai_bash_call("call_1", f"touch {sentinel}"),
        _openai_text("ok, stopping"),
    ])
    agent.hooks = {"PreToolUse": [{"matcher": "Bash", "command": "exit 2"}]}

    result = agent.run_task("touch the sentinel")

    assert result["success"] is True  # loop completed cleanly
    assert not sentinel.exists(), "blocked Bash must NOT have executed"

    verdicts = [e for e in agent.ledger.events
                if e.get("kind") == "tool" and e.get("tool_name") == "hook.PreToolUse"]
    assert verdicts, "a hook verdict event must be recorded"
    assert verdicts[0]["result"]["verdict"] == "BLOCKED"
    assert verdicts[0]["result"]["policy_hash"]


def test_no_hooks_configured_records_no_verdict_events(tmp_path):
    # With no hooks, the ledger must carry zero hook.* events (no behavior change).
    target = tmp_path / "made.txt"
    agent = _ScriptedAgent([
        _openai_bash_call("call_1", f"touch {target}"),
        _openai_text("done"),
    ])
    agent.hooks = {}  # explicitly none
    agent.run_task("touch a file")
    hook_events = [e for e in agent.ledger.events
                   if e.get("kind") == "tool" and str(e.get("tool_name", "")).startswith("hook.")]
    assert hook_events == []
