"""The edit-approval gate wired into the ToolGate pipeline.

Verifies the three integrated behaviors: refuse hard-blocked/sensitive edits,
record every decision to the ledger, and checkpoint-before-mutation in an
isolated worktree (and ONLY there — never committing to the user's branch).

These tests now call the gate adapter directly (EditPolicyGate.evaluate) rather
than the deleted _edit_policy_block agent method.
"""
from __future__ import annotations

import subprocess

from src.agent import KorgexAgent
from src.tool_gate import EditPolicyGate, GateContext


def _gate_ctx(tmp_path, policy="workspace", interactive=False, workspace_root=None,
              checkpoint=None, classify_edit=None):
    """Build a GateContext equivalent to what _agent(tmp_path, policy=...) would produce."""
    a = KorgexAgent(repo_root=str(tmp_path), interactive=interactive)
    a.edit_policy = policy
    a.workspace_root = workspace_root
    ctx = a._gate_context()
    # Allow test overrides for checkpoint and classify_edit.
    if checkpoint is not None or classify_edit is not None:
        import dataclasses
        ctx = dataclasses.replace(
            ctx,
            checkpoint=checkpoint if checkpoint is not None else ctx.checkpoint,
            classify_edit=classify_edit if classify_edit is not None else ctx.classify_edit,
        )
    return ctx


class FakeLedger:
    """Captures record_tool_call events instead of writing a journal."""

    def __init__(self):
        self.events = []

    def record_tool_call(self, **kw):
        self.events.append(kw)
        return len(self.events)


def _sink(led):
    """Return a sink function that records LedgerIntents into a FakeLedger."""
    from src.tool_gate import LedgerIntent  # noqa: F401
    def sink(intent):
        led.record_tool_call(
            tool_name=intent.tool_name, args=intent.args, result=intent.result,
            success=intent.success, duration_ms=0, triggered_by=1)
    return sink


def test_default_edit_policy_is_free(tmp_path, monkeypatch):
    # Out-of-the-box posture is FREE: act without prompting (thin floor only),
    # not the older WORKSPACE default that asked outside the repo.
    monkeypatch.delenv("KORGEX_EDIT_POLICY", raising=False)
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    assert a.edit_policy == "free"


def test_gate_refuses_hard_blocked_path_and_records_block(tmp_path):
    led = FakeLedger()
    ctx = _gate_ctx(tmp_path, policy="session")
    call = {"id": "1", "name": "Edit", "args": {"file_path": str(tmp_path / ".git" / "config")}}
    out = EditPolicyGate().evaluate(call, ctx)
    assert out.blocked and "refused" in out.block_result["error"]
    # Record manually via sink (gate returns the record; we forward it for the assertion)
    if out.record:
        _sink(led)(out.record)
    assert led.events[-1]["result"]["action"] == "block"
    assert led.events[-1]["success"] is False


def test_gate_blocks_sensitive_file_in_headless_session(tmp_path):
    ctx = _gate_ctx(tmp_path, policy="session", interactive=False)
    call = {"id": "1", "name": "Write", "args": {"file_path": str(tmp_path / ".env")}}
    out = EditPolicyGate().evaluate(call, ctx)
    assert out.blocked
    assert "sensitive" in out.record.result["action"]


def test_gate_allows_ordinary_workspace_edit_and_records_allow(tmp_path):
    ctx = _gate_ctx(tmp_path, policy="session")
    call = {"id": "1", "name": "Write", "args": {"file_path": str(tmp_path / "src" / "a.py")}}
    out = EditPolicyGate().evaluate(call, ctx)
    assert not out.blocked
    assert out.record.result["allowed"] is True and out.record.result["action"] == "allow"


def test_gate_ignores_non_file_tools_and_records_nothing(tmp_path):
    from src import tool_gate as tg
    ctx = _gate_ctx(tmp_path)
    assert EditPolicyGate().evaluate({"id": "1", "name": "Read", "args": {"file_path": "x"}}, ctx) is tg.ALLOW
    assert EditPolicyGate().evaluate({"id": "1", "name": "Bash", "args": {"command": "ls"}}, ctx) is tg.ALLOW


def test_no_checkpoint_in_place_but_checkpoints_in_isolated_worktree(tmp_path):
    # in-place (no workspace_root): allowed, but NO checkpoint (never commits to the user's branch)
    ctx = _gate_ctx(tmp_path, policy="session", workspace_root=None)
    call = {"id": "1", "name": "Write", "args": {"file_path": str(tmp_path / "a.py")}}
    out = EditPolicyGate().evaluate(call, ctx)
    assert out.record.result["checkpoint"] is None

    # isolated worktree (a real git repo as workspace_root): a checkpoint SHA is taken
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    (wt / "seed.txt").write_text("seed")
    ctx2 = _gate_ctx(tmp_path, policy="session", workspace_root=str(wt))
    out2 = EditPolicyGate().evaluate(
        {"id": "1", "name": "Write", "args": {"file_path": str(wt / "new.py")}}, ctx2)
    sha = out2.record.result["checkpoint"]
    assert sha and len(sha) == 40  # a real git SHA was recorded before the mutation
