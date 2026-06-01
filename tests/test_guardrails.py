"""
Guardrail-fence tests (Gate G — korgex can't weaken its own checks unsupervised).

Self-modifying agents have been observed disabling their own guardrails (the DGM
objective-hacking failure). Gate G fences the gate-enforcing code (agent loop,
ledger, hooks, isolation, test gate, eval harness) two ways:
1. classify_diff — a diff touching any protected path is flagged human-required;
   a leaf-only diff is auto-mergeable.
2. an in-run PreToolUse block — when self-coding unsupervised, a Write/Edit to a
   protected file is blocked outright and recorded as a PROTECTED_PATH verdict.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import guardrails as G  # noqa: E402
from src.agent import KorgexAgent  # noqa: E402


# ── 1. is_protected ───────────────────────────────────────────────────────

def test_is_protected_guardrail_files():
    assert G.is_protected("src/agent.py")
    assert G.is_protected("src/korg_ledger.py")
    assert G.is_protected("/Users/x/korgex/src/hooks.py")   # absolute also matches
    assert G.is_protected("src/workspace.py")
    assert G.is_protected("src/korgex_bench.py")


def test_is_protected_leaf_files_not_flagged():
    assert not G.is_protected("src/recall.py")
    assert not G.is_protected("docs/readme.md")
    assert not G.is_protected("src/vision.py")


# ── 2. classify_diff (the merge gate) ─────────────────────────────────────

def test_leaf_only_diff_is_auto_mergeable():
    c = G.classify_diff(["src/recall.py", "docs/x.md", "tests/test_recall.py"])
    assert c["auto_mergeable"] is True
    assert c["requires_human_review"] is False
    assert c["protected_hits"] == []


def test_diff_touching_guardrail_requires_human_review():
    c = G.classify_diff(["src/recall.py", "src/agent.py"])
    assert c["requires_human_review"] is True
    assert c["auto_mergeable"] is False
    assert "src/agent.py" in c["protected_hits"]


# ── 3. agent in-run block ─────────────────────────────────────────────────

class _FakeLedger:
    def __init__(self):
        self.events = []

    def record_user_prompt(self, prompt, triggered_by=None):
        self.events.append({"kind": "user_prompt"}); return 1

    def record_llm_call(self, **kw):
        self.events.append({"kind": "llm", **kw}); return 2

    def record_tool_call(self, **kw):
        self.events.append({"kind": "tool", **kw}); return None


def _write(call_id, file_path, content):
    msg = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
        id=call_id, function=SimpleNamespace(
            name="Write", arguments=json.dumps({"file_path": file_path, "content": content})))])
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=msg)])


def _text(t):
    return SimpleNamespace(usage=None,
                           choices=[SimpleNamespace(message=SimpleNamespace(content=t, tool_calls=None))])


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


def test_protected_path_write_is_blocked(tmp_path):
    target = tmp_path / "src" / "agent.py"
    agent = _ScriptedAgent([_write("c1", "src/agent.py", "MALICIOUS = True"),
                            _text("blocked, stopping")], repo_root=str(tmp_path))
    agent.protected_paths = G.DEFAULT_PROTECTED
    result = agent.run_task("try to weaken the guardrails")

    assert result["success"] is True            # loop fine; the one tool was blocked
    assert not target.exists(), "edit to a protected guardrail file must be blocked"
    verdicts = [e for e in agent.ledger.events
                if e.get("kind") == "tool" and e.get("tool_name") == "guardrail.block"]
    assert verdicts and verdicts[0]["result"]["verdict"] == "PROTECTED_PATH"


def test_leaf_path_write_is_allowed_under_fence(tmp_path):
    agent = _ScriptedAgent([_write("c1", "notes.txt", "hello"), _text("done")],
                           repo_root=str(tmp_path))
    agent.protected_paths = G.DEFAULT_PROTECTED
    agent.run_task("write a leaf file")
    assert (tmp_path / "notes.txt").read_text() == "hello"   # leaf write goes through


# ── 4. run_isolated_task attaches a merge gate ────────────────────────────

def _git_repo(tmp_path):
    r = tmp_path / "repo"; r.mkdir()
    def run(*a):
        subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    run("init", "-b", "main"); run("config", "user.email", "t@t.dev"); run("config", "user.name", "t")
    (r / "README.md").write_text("hi\n"); run("add", "-A"); run("commit", "-m", "init")
    return r


def test_isolated_run_flags_guardrail_edit_for_review(tmp_path):
    from src import workspace as W
    repo = _git_repo(tmp_path)
    # agent edits a guardrail file in the worktree (fence OFF → it lands, but the
    # merge gate must flag the branch as human-required)
    agent = _ScriptedAgent([_write("c1", "src/korg_ledger.py", "# tampered\n"), _text("done")],
                           repo_root=str(repo))
    result = agent.run_isolated_task("touch the ledger", worktree_path=str(tmp_path / "wt"))
    try:
        assert result["merge_gate"]["requires_human_review"] is True
        assert any("korg_ledger.py" in p for p in result["merge_gate"]["protected_hits"])
    finally:
        W.remove_worktree(str(repo), result["worktree"])
