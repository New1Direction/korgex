"""
korgex-bench tests (Gate E — the trust thermometer).

Measures korgex's self-coding success rate: each task runs end-to-end in an
isolated worktree (Gate A), graded by a HIDDEN test oracle (test-green == solved),
and three invariants must stay at zero — (1) no writes escaped the worktree,
(2) never success=true on a red gate, (3) never a null root_seq. The harness is
agent-agnostic (injectable runner), so the scoring/invariant logic is fully
testable here without a live model; production plugs in the real KorgexAgent.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import korgex_bench as B  # noqa: E402


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


def _ok_result():
    return {"success": True, "root_seq": 1, "test_gate": {"passed": True}}


# ── per-task evaluation ───────────────────────────────────────────────────

def test_eval_passes_when_oracle_satisfied(tmp_path):
    repo = _git_repo(tmp_path)

    def solver(prompt, worktree):
        (Path(worktree) / "solution.txt").write_text("done")
        return _ok_result()

    task = B.Task(id="t1", band="leaf", prompt="create solution.txt",
                  verify_command="test -f solution.txt")
    r = B.run_task_eval(task, solver, str(repo), worktree_path=str(tmp_path / "wt1"))
    assert r["passed"] is True
    assert all(r["invariants"].values())  # every invariant clean


def test_eval_fails_when_task_unsolved(tmp_path):
    repo = _git_repo(tmp_path)

    def noop(prompt, worktree):
        return _ok_result()

    task = B.Task(id="t2", band="leaf", prompt="do nothing",
                  verify_command="test -f solution.txt")
    r = B.run_task_eval(task, noop, str(repo), worktree_path=str(tmp_path / "wt2"))
    assert r["passed"] is False


def test_eval_flags_out_of_worktree_write(tmp_path):
    repo = _git_repo(tmp_path)

    def leaker(prompt, worktree):
        (Path(repo) / "leak.py").write_text("escaped the worktree!")  # writes to SOURCE
        return _ok_result()

    task = B.Task(id="t3", band="leaf", prompt="x", verify_command="true")
    r = B.run_task_eval(task, leaker, str(repo), worktree_path=str(tmp_path / "wt3"))
    assert r["invariants"]["no_escape"] is False


def test_eval_flags_null_root_seq(tmp_path):
    repo = _git_repo(tmp_path)

    def no_ledger(prompt, worktree):
        return {"success": True, "root_seq": None, "test_gate": {"passed": True}}

    task = B.Task(id="t4", band="leaf", prompt="x", verify_command="true")
    r = B.run_task_eval(task, no_ledger, str(repo), worktree_path=str(tmp_path / "wt4"))
    assert r["invariants"]["durable_ledger"] is False


def test_eval_flags_green_on_red(tmp_path):
    repo = _git_repo(tmp_path)

    def liar(prompt, worktree):
        return {"success": True, "root_seq": 1, "test_gate": {"passed": False}}

    task = B.Task(id="t5", band="leaf", prompt="x", verify_command="true")
    r = B.run_task_eval(task, liar, str(repo), worktree_path=str(tmp_path / "wt5"))
    assert r["invariants"]["no_green_on_red"] is False


def test_setup_creates_the_unsolved_state(tmp_path):
    repo = _git_repo(tmp_path)
    seen = {}

    def setup(worktree):
        (Path(worktree) / "broken.txt").write_text("needs fixing")

    def solver(prompt, worktree):
        seen["had_broken"] = (Path(worktree) / "broken.txt").exists()
        (Path(worktree) / "fixed.txt").write_text("ok")
        return _ok_result()

    task = B.Task(id="t6", band="leaf", prompt="fix it",
                  verify_command="test -f fixed.txt", setup=setup)
    r = B.run_task_eval(task, solver, str(repo), worktree_path=str(tmp_path / "wt6"))
    assert seen["had_broken"] is True   # setup ran before the agent
    assert r["passed"] is True


# ── bench aggregation / scorecard ─────────────────────────────────────────

def test_run_bench_scorecard(tmp_path):
    repo = _git_repo(tmp_path)

    def solver(prompt, worktree):
        (Path(worktree) / "solution.txt").write_text("x")
        return _ok_result()

    tasks = [
        B.Task("a", "leaf", "", "test -f solution.txt"),
        B.Task("b", "cross-module", "", "test -f solution.txt"),
        B.Task("c", "leaf", "", "test -f NOPE.txt"),   # solver won't satisfy this
    ]
    rep = B.run_bench(tasks, solver, str(repo), worktree_base=str(tmp_path / "wts"))

    assert rep["total"] == 3
    assert rep["resolved"] == 2
    assert rep["resolved_pct"] == pytest.approx(66.7, abs=0.1)
    assert rep["by_band"]["leaf"]["total"] == 2 and rep["by_band"]["leaf"]["resolved"] == 1
    assert rep["by_band"]["cross-module"]["resolved"] == 1
    # the three hard invariants must all be zero for a trustworthy run
    assert rep["invariant_violations"] == {"no_escape": 0, "no_green_on_red": 0, "durable_ledger": 0}


def test_seed_tasks_are_wellformed():
    # the shipped seed set must be valid Task specs so the harness can run them
    assert len(B.SEED_TASKS) >= 1
    for t in B.SEED_TASKS:
        assert t.id and t.prompt and t.verify_command and t.band
