"""
Auditable memory recall in the agent loop (roadmap idea #5).

The differentiator nobody else has: not "agent memory" (Mem0/OpenMemory own that
wedge) but AUDITABLE memory. At task entry korgex recalls its memories, verifies
each anchored one against its source baseline, injects only the fresh facts, and
WITHHOLDS stale ones — recording a `memory_reconcile` decision to the
hash-chained ledger. The agent's beliefs become verifiable and their drift goes
on the record.

`recall_block` is the pure core (memories + a record_event sink → prompt block +
reconcile events); the agent method wires it to the live memory store + ledger.
"""

import hashlib
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import memory_drift as D  # noqa: E402


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _recorder():
    events, seq = [], [0]

    def record(tool_name, args, result, success, triggered_by):
        seq[0] += 1
        events.append({"seq": seq[0], "tool_name": tool_name, "args": args,
                       "result": result, "success": success, "triggered_by": triggered_by})
        return seq[0]

    return events, record


def test_recall_block_injects_fresh_and_unanchored_withholds_stale(tmp_path):
    cfg = tmp_path / "build.sh"
    cfg.write_text("make build\n")
    fresh_sha = _sha(cfg.read_bytes())
    moved = tmp_path / "test.sh"
    moved.write_text("bazel test //...\n")          # current
    memories = [
        {"name": "build-cmd", "description": "how to build", "body": "Run make build.",
         "source": str(cfg), "source_sha": fresh_sha},                     # anchored fresh
        {"name": "test-cmd", "description": "how to test", "body": "Run make test.",
         "source": str(moved), "source_sha": _sha(b"OLD")},                # anchored DRIFTED
        {"name": "user-style", "description": "user prefers terse output", "body": "Be terse."},  # unanchored
    ]
    events, record = _recorder()
    out = D.recall_block(memories, repo_root=str(tmp_path), record_event=record, triggered_by=7)

    assert "build-cmd" in out["injected"]
    assert "user-style" in out["injected"]          # unanchored facts still injected
    assert "test-cmd" not in out["injected"]        # stale → withheld
    assert out["flagged"] == ["test-cmd"]
    assert "make build" in out["block"]
    assert "make test" not in out["block"]          # the stale fact is NOT in the prompt


def test_recall_block_records_chained_reconcile_for_stale(tmp_path):
    gone = tmp_path / "gone.txt"
    gone.write_text("x")
    baseline = _sha(b"x")
    gone.unlink()                                    # source missing now
    memories = [{"name": "ref", "description": "a ref", "body": "...",
                 "source": str(gone), "source_sha": baseline}]
    events, record = _recorder()
    out = D.recall_block(memories, repo_root=str(tmp_path), record_event=record, triggered_by=3)

    assert out["injected"] == []
    assert out["flagged"] == ["ref"]
    rec = [e for e in events if e["tool_name"] == "memory_reconcile"]
    assert len(rec) == 1
    assert rec[0]["args"]["decision"] == "flag"
    assert rec[0]["args"]["memory_name"] == "ref"
    assert rec[0]["triggered_by"] == 3               # chained off the task prompt


def test_recall_block_no_memories_is_empty():
    out = D.recall_block([], repo_root=".", record_event=None, triggered_by=1)
    assert out["block"] == "" and out["injected"] == [] and out["flagged"] == []


# ── agent wiring: _recall_and_reconcile against a live memory store ─────────

class _FakeLedger:
    def __init__(self):
        self.events, self.seq = [], 0

    def record_tool_call(self, **kw):
        self.seq += 1
        self.events.append({"seq": self.seq, **kw})
        return self.seq


def test_agent_recall_and_reconcile_end_to_end(tmp_path):
    from src import memory as M
    from src.agent import KorgexAgent

    M.init_memory(project_root=str(tmp_path))
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text("[tool.pytest]\n")
    M.save_memory("test-cmd", "how tests run here", "project", "Run pytest.", source=str(cfg))
    M.save_memory("tone", "user prefers concise answers", "user", "Keep it short.")
    cfg.write_text("[tool.pytest]\naddopts='-q'\n")   # drift the anchored memory's source

    a = KorgexAgent(model="gpt-4o", repo_root=str(tmp_path), interactive=False)
    led = _FakeLedger()
    block = a._recall_and_reconcile(led, prompt_seq=1)

    assert "Keep it short" in block                  # unanchored user memory injected
    assert "Run pytest" not in block                 # stale anchored memory withheld
    recon = [e for e in led.events if e["tool_name"] == "memory_reconcile"]
    assert any(e["args"]["memory_name"] == "test-cmd" and e["args"]["decision"] == "flag"
               for e in recon)


def test_recall_never_crashes_the_loop(tmp_path, monkeypatch):
    # Recall is an enhancement, not core: a failure in the memory subsystem
    # (a missing optional dep like PyYAML, an unreadable store) must degrade to
    # no recall, never crash run_task. Regression: Gate F caught run_task
    # importing src.memory → PyYAML, fatal on a clean install.
    from src import memory as M
    from src import memory_drift as D
    from src.agent import KorgexAgent

    M.init_memory(project_root=str(tmp_path))
    M.save_memory("x", "a stored memory", "user", "body")

    def _boom(*a, **k):
        raise RuntimeError("simulated memory-subsystem failure")

    monkeypatch.setattr(D, "recall_block", _boom)
    a = KorgexAgent(model="gpt-4o", repo_root=str(tmp_path), interactive=False)
    assert a._recall_and_reconcile(_FakeLedger(), prompt_seq=1) == ""  # no raise
