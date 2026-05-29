"""
Best-of-N self-coding tests (roadmap #4 — the ultracode payoff).

Inference-time scaling for reliability: run K independent attempts at the SAME
task, each in its OWN isolated worktree (so they can't collide), gate each, and
pick a winner that passed. Composed entirely from pieces already shipped —
parallel() + worktrees + the test-gate verdict (success==gate-pass) + the merge
gate. Agent-agnostic runner, so selection/parallelism is testable without an LLM.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.korgantic import run_best_of_n  # noqa: E402


def _git_repo(tmp_path):
    r = tmp_path / "repo"; r.mkdir()
    def run(*a):
        subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    run("init", "-b", "main"); run("config", "user.email", "t@t.dev"); run("config", "user.name", "t")
    (r / "README.md").write_text("hi\n"); run("add", "-A"); run("commit", "-m", "init")
    return r


def test_best_of_n_picks_a_passing_attempt(tmp_path):
    repo = _git_repo(tmp_path)

    def runner(prompt, wt):
        (Path(wt) / "notes.txt").write_text("did the thing")
        return {"success": True, "root_seq": 1}

    out = run_best_of_n("do x", runner, str(repo), n=3, worktree_base=str(tmp_path / "w"))
    assert out["n"] == 3 and len(out["attempts"]) == 3
    assert out["passed_count"] == 3
    assert out["winner"] is not None
    assert out["winner"]["merge_gate"]["auto_mergeable"] is True  # leaf edit


def test_best_of_n_no_winner_when_all_fail(tmp_path):
    repo = _git_repo(tmp_path)

    def runner(prompt, wt):
        return {"success": False, "root_seq": 1}   # gate red → not a winner

    out = run_best_of_n("x", runner, str(repo), n=3, worktree_base=str(tmp_path / "w"))
    assert out["passed_count"] == 0
    assert out["winner"] is None


def test_best_of_n_counts_mixed_outcomes(tmp_path):
    repo = _git_repo(tmp_path)
    lock = threading.Lock()
    state = {"i": 0}

    def runner(prompt, wt):
        with lock:
            state["i"] += 1
            i = state["i"]
        (Path(wt) / "notes.txt").write_text("x")
        return {"success": i <= 2, "root_seq": 1}   # exactly 2 of 4 pass

    out = run_best_of_n("x", runner, str(repo), n=4, worktree_base=str(tmp_path / "w"))
    assert out["passed_count"] == 2
    assert out["winner"] is not None


def test_best_of_n_winner_even_if_not_auto_mergeable(tmp_path):
    repo = _git_repo(tmp_path)

    def runner(prompt, wt):
        # every passing attempt touches a guardrail file → none auto-mergeable
        os.makedirs(os.path.join(wt, "src"), exist_ok=True)
        Path(wt, "src", "agent.py").write_text("# tweak\n")
        return {"success": True, "root_seq": 1}

    out = run_best_of_n("x", runner, str(repo), n=2, worktree_base=str(tmp_path / "w"))
    assert out["passed_count"] == 2
    assert out["winner"] is not None                       # falls back to a passing attempt
    assert out["winner"]["merge_gate"]["requires_human_review"] is True
