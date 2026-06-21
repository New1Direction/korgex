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


# ── verifiable trail: the N attempts + the pick become tamper-evident events ───

def test_best_of_n_records_a_verifiable_trail(tmp_path):
    from src import korg_ledger as KL
    from src import ledger_spec as S
    repo = _git_repo(tmp_path)
    jp = str(tmp_path / "j.jsonl")
    led = KL.LocalJournalClient(journal_path=jp)
    root = led.record_user_prompt("ship feature X")

    def runner(prompt, wt):
        (Path(wt) / "notes.txt").write_text("did it")
        return {"success": True, "root_seq": 1}

    out = run_best_of_n("ship X", runner, str(repo), n=3, worktree_base=str(tmp_path / "w"),
                        ledger=led, parent_seq=root)
    assert "root_seq" in out
    events = KL.load_journal_raw(jp)
    kinds = [e.get("tool_name") for e in events]
    assert kinds.count("best_of_n.attempt") == 3           # one verifiable event per attempt
    sel = next(e for e in events if e.get("tool_name") == "best_of_n.selected")
    assert sel["result"]["passed_count"] == 3
    assert sel["result"]["winner_branch"] is not None      # the pick is named
    assert S.verify_chain(events) == []                    # …and the whole trail's chain is intact


def test_best_of_n_records_setup_failures_as_attempts(tmp_path, monkeypatch):
    from src import korg_ledger as KL
    from src import workspace as W
    repo = _git_repo(tmp_path)
    jp = str(tmp_path / "j.jsonl")
    led = KL.LocalJournalClient(journal_path=jp)

    real_remove = W.remove_worktree

    def create_worktree(repo_root, branch, worktree_path=None, base="HEAD"):
        if branch.endswith("-1"):
            raise RuntimeError("simulated worktree metadata race")
        wt = worktree_path or str(tmp_path / branch.replace("/", "_"))
        Path(wt).mkdir(parents=True, exist_ok=True)
        return wt

    monkeypatch.setattr(W, "create_worktree", create_worktree)
    monkeypatch.setattr(W, "changed_paths", lambda wt: ["notes.txt"])
    monkeypatch.setattr(W, "remove_worktree", lambda repo_root, wt: real_remove(repo_root, wt))

    def runner(prompt, wt):
        (Path(wt) / "notes.txt").write_text("did it")
        return {"success": True, "root_seq": 1}

    out = run_best_of_n("ship X", runner, str(repo), n=3, worktree_base=str(tmp_path / "w"),
                        ledger=led)

    assert len(out["attempts"]) == 3
    assert out["passed_count"] == 2
    failed = [a for a in out["attempts"] if not a["passed"]]
    assert len(failed) == 1
    assert "worktree setup failed" in failed[0]["result"]["error"]
    events = KL.load_journal_raw(jp)
    attempt_events = [e for e in events if e.get("tool_name") == "best_of_n.attempt"]
    assert len(attempt_events) == 3
    assert any("simulated worktree metadata race" in (e.get("result", {}).get("error") or "")
               for e in attempt_events)


def test_best_of_n_without_ledger_is_unchanged(tmp_path):
    repo = _git_repo(tmp_path)

    def runner(prompt, wt):
        (Path(wt) / "n.txt").write_text("x")
        return {"success": True, "root_seq": 1}

    out = run_best_of_n("x", runner, str(repo), n=2, worktree_base=str(tmp_path / "w"))
    assert "root_seq" not in out                           # no ledger → no recording (back-compat)
    assert out["passed_count"] == 2
