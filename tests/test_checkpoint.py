"""
Checkpoint + rewind tests (Gate C — bind ledger rewind to the FILESYSTEM).

rewind_events/verify_dag truncate the event list but never touch disk, so a
failed self-edit left the worktree dirty with no way back. Gate C records a git
snapshot per checkpoint and makes 'rewind to seq N' restore BOTH the worktree
(git reset --hard + clean) AND the ledger DAG — turning the conceptual rewind
into real recovery.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import workspace as W  # noqa: E402
from src.korg_ledger import verify_dag  # noqa: E402


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


# ── 1. git checkpoint / restore round-trip ────────────────────────────────

def test_git_checkpoint_and_restore_roundtrip(tmp_path):
    repo = _git_repo(tmp_path)
    sha = W.git_checkpoint(str(repo))           # snapshot clean state
    assert sha and len(sha) >= 7

    # mutate: modify a tracked file + add a new one
    (repo / "README.md").write_text("CHANGED\n")
    (repo / "new.py").write_text("print('x')\n")

    W.git_restore(str(repo), sha)               # roll back
    assert (repo / "README.md").read_text() == "hi\n"   # tracked change reverted
    assert not (repo / "new.py").exists()               # new file removed


# ── 2. checkpointer: rewind restores worktree AND truncates ledger ────────

def test_rewind_restores_worktree_and_truncates_ledger(tmp_path):
    repo = _git_repo(tmp_path)
    wt = W.create_worktree(str(repo), "korgex/gate-c", worktree_path=str(tmp_path / "wt"))
    try:
        cp = W.WorkspaceCheckpointer(wt)
        cp.snapshot(1)                          # pre-edit checkpoint at seq 1

        (Path(wt) / "feature.py").write_text("print('hi')\n")
        (Path(wt) / "README.md").write_text("edited by the agent\n")
        cp.snapshot(5)                          # post-edit checkpoint at seq 5

        events = [{"seq_id": 1, "triggered_by": None},
                  {"seq_id": 5, "triggered_by": 1}]
        out = cp.rewind_to(1, events=events)    # rewind to pre-edit state

        # (a) the WORKTREE is restored to the pre-edit tree
        assert not (Path(wt) / "feature.py").exists()
        assert (Path(wt) / "README.md").read_text() == "hi\n"
        # (b) the ledger is truncated and still a valid DAG
        assert [e["seq_id"] for e in out["events"]] == [1]
        assert verify_dag(out["events"]) == []
        assert out["restored_to"][0] == 1       # restored to the seq-1 checkpoint
    finally:
        W.remove_worktree(str(repo), wt)


def test_rewind_to_seq_before_any_checkpoint_is_safe(tmp_path):
    repo = _git_repo(tmp_path)
    wt = W.create_worktree(str(repo), "korgex/gate-c2", worktree_path=str(tmp_path / "wt2"))
    try:
        cp = W.WorkspaceCheckpointer(wt)
        cp.snapshot(10)
        out = cp.rewind_to(3, events=[{"seq_id": 1, "triggered_by": None}])
        # no checkpoint <= 3 → workspace untouched, no crash
        assert out["restored_to"] is None
        assert [e["seq_id"] for e in out["events"]] == [1]
    finally:
        W.remove_worktree(str(repo), wt)
