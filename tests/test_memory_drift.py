"""
Memory drift — the genuine differentiator (roadmap P2 #5).

Incumbents punt on the trust-hierarchy problem: a remembered fact/instruction
silently goes stale as the codebase moves, and you find out when the agent acts
on it. korgex makes staleness an EXACT signal and the reconciliation an
AUDITABLE one:

  1. anchor — a memory records the sha256 of what it was derived from at write
     time (memory.save_memory(..., source=...));
  2. detect — check_drift / scan recompute the source and compare, so drift is a
     content-hash fact, not a guess;
  3. reconcile — the keep / refresh / discard decision is written to the ledger
     (memory_reconcile event), so it rides the tamper-evident hash-chain and is
     replayable. The decision is the audit answer.

Engine is pure + dependency-light; the ledger write goes through the same
LocalJournalClient every other event uses (so verify_chain covers it too).
"""

import hashlib
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import memory_drift as D  # noqa: E402
from src import korg_ledger as L  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── baseline computation ────────────────────────────────────────────────────

def test_compute_baseline_of_a_file(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text("test_cmd = 'pytest'\n")
    assert D.compute_baseline(str(f)) == _sha(f.read_bytes())


def test_compute_baseline_of_a_fact():
    # a non-file source: the literal fact itself is the baseline
    assert D.compute_baseline("fact:tests live in tests/") == _sha(b"tests live in tests/")


def test_compute_baseline_missing_file_is_none(tmp_path):
    assert D.compute_baseline(str(tmp_path / "nope.txt")) is None


# ── drift detection ─────────────────────────────────────────────────────────

def test_check_drift_fresh(tmp_path):
    f = tmp_path / "build.sh"
    f.write_text("make build\n")
    v = D.check_drift(str(f), _sha(f.read_bytes()))
    assert v["drifted"] is False
    assert v["status"] == "fresh"


def test_check_drift_detects_change(tmp_path):
    f = tmp_path / "build.sh"
    f.write_text("make build\n")
    baseline = _sha(f.read_bytes())
    f.write_text("bazel build //...\n")          # the world moved on
    v = D.check_drift(str(f), baseline)
    assert v["drifted"] is True
    assert v["status"] == "drifted"
    assert v["current_sha"] != baseline


def test_check_drift_missing_source(tmp_path):
    f = tmp_path / "gone.txt"
    f.write_text("x")
    baseline = _sha(f.read_bytes())
    f.unlink()
    v = D.check_drift(str(f), baseline)
    assert v["drifted"] is True
    assert v["status"] == "missing"


def test_check_drift_unanchored_cannot_verify(tmp_path):
    f = tmp_path / "thing.txt"
    f.write_text("x")
    v = D.check_drift(str(f), baseline_sha=None)
    assert v["drifted"] is None                   # unknown, not false
    assert v["status"] == "unanchored"


# ── scan across all memories ────────────────────────────────────────────────

def test_scan_partitions_memories_by_drift_status(tmp_path):
    fresh = tmp_path / "a.txt"; fresh.write_text("a")
    moved = tmp_path / "b.txt"; moved.write_text("b")
    moved_baseline = _sha(b"old-b")               # never matched current
    memories = [
        {"name": "mem-fresh", "source": str(fresh), "source_sha": _sha(b"a")},
        {"name": "mem-drifted", "source": str(moved), "source_sha": moved_baseline},
        {"name": "mem-loose", "description": "no source at all"},
    ]
    report = D.scan(memories, repo_root=str(tmp_path))
    assert report["fresh"] == ["mem-fresh"]
    assert report["drifted"] == ["mem-drifted"]
    assert report["unanchored"] == ["mem-loose"]
    assert report["has_drift"] is True


# ── reconcile decision = auditable, hash-chained ledger event ───────────────

def test_record_reconcile_writes_a_chained_event(tmp_path):
    jp = tmp_path / "journal.jsonl"
    led = L.LocalJournalClient(journal_path=str(jp))
    seq = D.record_reconcile(led, memory_name="mem-drifted", decision="refresh",
                             baseline_sha="old", current_sha="new")
    assert isinstance(seq, int)
    events = [__import__("json").loads(x) for x in jp.read_text().splitlines() if x.strip()]
    ev = events[-1]
    assert ev["tool_name"] == "memory_reconcile"
    assert ev["args"]["decision"] == "refresh"
    assert ev["args"]["memory_name"] == "mem-drifted"
    # the decision is now part of the tamper-evident chain
    assert L.verify_chain(events) == []


def test_record_reconcile_rejects_unknown_decision(tmp_path):
    led = L.LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"))
    with pytest.raises(ValueError):
        D.record_reconcile(led, "m", decision="yolo")


def test_reconcile_refresh_returns_new_baseline(tmp_path):
    f = tmp_path / "build.sh"; f.write_text("make build\n")
    baseline = _sha(f.read_bytes())
    f.write_text("bazel build //...\n")           # drifted
    led = L.LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"))
    mem = {"name": "build-cmd", "source": str(f), "source_sha": baseline}

    out = D.reconcile(mem, decision="refresh", ledger=led)
    assert out["decision"] == "refresh"
    assert out["verdict"]["drifted"] is True
    assert out["new_source_sha"] == _sha(f.read_bytes())   # caller rewrites memory
    assert isinstance(out["seq_id"], int)


def test_reconcile_discard_signals_deletion(tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    led = L.LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"))
    mem = {"name": "stale", "source": str(f), "source_sha": _sha(b"old")}
    out = D.reconcile(mem, decision="discard", ledger=led)
    assert out["delete"] is True


# ── memory.py anchors a baseline at write time (end-to-end) ─────────────────

def test_save_memory_anchors_source_and_scan_detects_later_drift(tmp_path):
    from src import memory as M
    M.init_memory(project_root=str(tmp_path))
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text("[tool.pytest]\n")

    res = M.save_memory("test-cmd", "how tests are run in this repo",
                        "project", "Run `pytest`.", source=str(cfg))
    assert res["success"], res

    mem = M.read_memory("test-cmd")
    assert mem["source"] == str(cfg)
    assert mem["source_sha"] == _sha(cfg.read_bytes())

    # fresh right after writing
    assert D.scan([mem], repo_root=str(tmp_path))["fresh"] == ["test-cmd"]

    cfg.write_text("[tool.pytest]\naddopts = '-q'\n")   # the config moved on
    later = D.scan([M.read_memory("test-cmd")], repo_root=str(tmp_path))
    assert later["drifted"] == ["test-cmd"]
    assert later["has_drift"] is True


def test_cli_drift_command_exit_codes(tmp_path, monkeypatch, capsys):
    from src import cli
    from src import memory as M
    monkeypatch.chdir(tmp_path)
    M.init_memory(project_root=str(tmp_path))
    cfg = tmp_path / "cfg.txt"; cfg.write_text("v1")
    M.save_memory("cfg-val", "the config value", "project", "value is v1",
                  source=str(cfg))

    monkeypatch.setattr(cli.sys, "argv", ["korgex", "drift"])
    assert cli.main() == 0
    assert "no drift" in capsys.readouterr().out.lower()

    cfg.write_text("v2")                                 # drift it
    monkeypatch.setattr(cli.sys, "argv", ["korgex", "drift"])
    assert cli.main() == 1
    out = capsys.readouterr().out.lower()
    assert "drift" in out and "cfg-val" in out
