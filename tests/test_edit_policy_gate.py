"""The edit-approval gate wired into KorgexAgent's tool loop.

Verifies the three integrated behaviors: refuse hard-blocked/sensitive edits,
record every decision to the ledger, and checkpoint-before-mutation in an
isolated worktree (and ONLY there — never committing to the user's branch).
"""
from __future__ import annotations

import subprocess

from src.agent import KorgexAgent


class FakeLedger:
    """Captures record_tool_call events instead of writing a journal."""

    def __init__(self):
        self.events = []

    def record_tool_call(self, **kw):
        self.events.append(kw)
        return len(self.events)


def _agent(tmp_path, policy="workspace", interactive=False, workspace_root=None):
    a = KorgexAgent(repo_root=str(tmp_path), interactive=interactive)
    a.edit_policy = policy
    a.workspace_root = workspace_root
    return a


def test_gate_refuses_hard_blocked_path_and_records_block(tmp_path):
    a = _agent(tmp_path, policy="session")
    led = FakeLedger()
    call = {"name": "Edit", "args": {"file_path": str(tmp_path / ".git" / "config")}}
    block = a._edit_policy_block(call, led, 1)
    assert block is not None and "refused" in block["error"]
    assert led.events[-1]["result"]["action"] == "block"
    assert led.events[-1]["success"] is False


def test_gate_blocks_sensitive_file_in_headless_session(tmp_path):
    a = _agent(tmp_path, policy="session", interactive=False)
    led = FakeLedger()
    call = {"name": "Write", "args": {"file_path": str(tmp_path / ".env")}}
    assert a._edit_policy_block(call, led, 1) is not None
    assert "sensitive" in led.events[-1]["result"]["action"]


def test_gate_allows_ordinary_workspace_edit_and_records_allow(tmp_path):
    a = _agent(tmp_path, policy="session")
    led = FakeLedger()
    call = {"name": "Write", "args": {"file_path": str(tmp_path / "src" / "a.py")}}
    assert a._edit_policy_block(call, led, 1) is None
    ev = led.events[-1]["result"]
    assert ev["allowed"] is True and ev["action"] == "allow"


def test_gate_ignores_non_file_tools_and_records_nothing(tmp_path):
    a = _agent(tmp_path)
    led = FakeLedger()
    assert a._edit_policy_block({"name": "Read", "args": {"file_path": "x"}}, led, 1) is None
    assert a._edit_policy_block({"name": "Bash", "args": {"command": "ls"}}, led, 1) is None
    assert led.events == []


def test_no_checkpoint_in_place_but_checkpoints_in_isolated_worktree(tmp_path):
    # in-place (no workspace_root): allowed, but NO checkpoint (never commits to the user's branch)
    a = _agent(tmp_path, policy="session", workspace_root=None)
    led = FakeLedger()
    a._edit_policy_block({"name": "Write", "args": {"file_path": str(tmp_path / "a.py")}}, led, 1)
    assert led.events[-1]["result"]["checkpoint"] is None

    # isolated worktree (a real git repo as workspace_root): a checkpoint SHA is taken
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    (wt / "seed.txt").write_text("seed")
    b = _agent(tmp_path, policy="session", workspace_root=str(wt))
    led2 = FakeLedger()
    b._edit_policy_block({"name": "Write", "args": {"file_path": str(wt / "new.py")}}, led2, 1)
    sha = led2.events[-1]["result"]["checkpoint"]
    assert sha and len(sha) == 40  # a real git SHA was recorded before the mutation
