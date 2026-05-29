"""
Workspace-isolation tests (Gate A — the #1 safety fix for self-coding).

korgex currently edits the live working copy on the host (SANDBOX/REPO_ROOT
never initialized). For autonomous self-coding it must edit an ISOLATED git
worktree, and writes must provably never escape that worktree. These cover:
1. path_within — the boundary guard (absolute / ../ escapes rejected).
2. create_worktree / remove_worktree — real git worktree lifecycle.
3. run_isolated_task — a write-heavy run leaves the SOURCE checkout untouched
   (the Gate A acceptance test) and a write outside the worktree is blocked.
"""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import workspace as W  # noqa: E402
from src.agent import KorgexAgent  # noqa: E402


def _git_repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    def run(*a):
        subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "t@t.dev")
    run("config", "user.name", "t")
    (r / "README.md").write_text("hi\n")
    run("add", "-A")
    run("commit", "-m", "init")
    return r


def _status(repo):
    return subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip()


# ── 1. path_within boundary guard ─────────────────────────────────────────

def test_path_within_allows_relative_inside(tmp_path):
    assert W.path_within(str(tmp_path), "a.py") is True
    assert W.path_within(str(tmp_path), "sub/dir/a.py") is True


def test_path_within_rejects_parent_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert W.path_within(str(root), "../escape.py") is False


def test_path_within_rejects_absolute_outside(tmp_path):
    assert W.path_within(str(tmp_path), "/etc/passwd") is False


def test_path_within_allows_absolute_inside(tmp_path):
    inside = str(tmp_path / "x.py")
    assert W.path_within(str(tmp_path), inside) is True


# ── 2. worktree lifecycle (real git) ──────────────────────────────────────

def test_create_and_remove_worktree(tmp_path):
    repo = _git_repo(tmp_path)
    wt = W.create_worktree(str(repo), "korgex/test-feature",
                           worktree_path=str(tmp_path / "wt"))
    assert os.path.isdir(wt)
    assert os.path.isfile(os.path.join(wt, "README.md"))  # checked out from base
    # it's on its own branch, and the source checkout is untouched
    branch = subprocess.run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    assert branch == "korgex/test-feature"
    assert _status(repo) == ""

    W.remove_worktree(str(repo), wt)
    assert not os.path.isdir(wt)


# ── 3. agent integration: isolation + guard ───────────────────────────────

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

    def _call(self, client, messages, tools, output_schema=None, system_prompt=None):
        return self._responses.pop(0)


def test_run_isolated_task_leaves_source_checkout_clean(tmp_path):
    repo = _git_repo(tmp_path)
    agent = _ScriptedAgent(
        [_openai_write("c1", "feature.py", "print('hi')"), _openai_text("done")],
        repo_root=str(repo),
    )
    result = agent.run_isolated_task("add feature.py", worktree_path=str(tmp_path / "wt"))
    wt = result["worktree"]
    try:
        assert result["branch"]
        # the write landed in the WORKTREE
        assert os.path.isfile(os.path.join(wt, "feature.py"))
        # the SOURCE checkout is untouched — the Gate A guarantee
        assert not (repo / "feature.py").exists()
        assert _status(repo) == "", f"source checkout dirtied: {_status(repo)}"
    finally:
        W.remove_worktree(str(repo), wt)


def test_workspace_guard_blocks_write_escaping_root(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    escape = tmp_path / "escape.py"  # sibling, outside ws
    agent = _ScriptedAgent(
        [_openai_write("c1", str(escape), "MALICIOUS"), _openai_text("stopped")],
    )
    agent.workspace_root = str(ws)
    result = agent.run_task("try to escape")

    assert result["success"] is True   # loop completed; the tool was just blocked
    assert not escape.exists(), "write escaped the workspace root!"
    blocked = [e for e in agent.ledger.events
               if e.get("kind") == "tool" and "workspace" in json.dumps(e.get("result", {})).lower()]
    assert blocked, "a workspace-violation verdict should be recorded"
