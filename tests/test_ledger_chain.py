"""
Tamper-evident ledger — hash-chain tests.

`verify_dag` already proves the journal is *well-formed* (unique seqs, strictly
backward causal edges). That makes rewind sound, but it does NOT prove the
journal wasn't doctored: hand-edit a field, delete a row, or splice in a forged
event and `verify_dag` happily passes as long as the seqs/edges stay consistent.

These tests pin the cryptographic layer that closes that gap:
  - every event carries prev_hash + entry_hash, chaining each entry to the last;
  - verify_chain recomputes the chain and flags ANY edit / delete / insert /
    reorder, localized to the offending seq;
  - with an HMAC key set, the chain is not just tamper-EVIDENT but tamper-PROOF:
    an attacker who rewrites the tail can't forge valid entry_hashes without the
    key, so plain-sha256 recomputation fails under keyed verification;
  - LocalJournalClient writes a live chain and continues it across a restart.

This is what turns "we have an audit log" (everyone does) into "we have a
cognition ledger you can prove is intact" (the moat).
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import korg_ledger as L  # noqa: E402


def _chain(events, key=None):
    """Helper: stamp prev_hash/entry_hash onto a list of bare event dicts."""
    prev = L.GENESIS_HASH
    out = []
    for e in events:
        e = dict(e)
        e["prev_hash"] = prev
        e["entry_hash"] = L.chain_hash(e, key=key)
        prev = e["entry_hash"]
        out.append(e)
    return out


# ── chain_hash primitive ───────────────────────────────────────────────────

def test_chain_hash_is_deterministic_and_excludes_its_own_hash():
    e = {"seq_id": 1, "tool_name": "Write", "prev_hash": L.GENESIS_HASH}
    h1 = L.chain_hash(e)
    # adding entry_hash must not change the preimage (it's excluded)
    e2 = dict(e, entry_hash="whatever")
    assert L.chain_hash(e2) == h1
    # but changing real content does
    e3 = dict(e, tool_name="Edit")
    assert L.chain_hash(e3) != h1


def test_chain_hash_depends_on_prev_hash():
    base = {"seq_id": 2, "tool_name": "Read"}
    a = L.chain_hash(dict(base, prev_hash="a" * 64))
    b = L.chain_hash(dict(base, prev_hash="b" * 64))
    assert a != b


# ── verify_chain: happy path ────────────────────────────────────────────────

def test_verify_chain_accepts_intact_chain():
    events = _chain([
        {"seq_id": 1, "tool_name": "user_prompt"},
        {"seq_id": 2, "tool_name": "llm_inference", "triggered_by": 1},
        {"seq_id": 3, "tool_name": "Write", "triggered_by": 2},
    ])
    assert L.verify_chain(events) == []


# ── verify_chain: tamper detection ──────────────────────────────────────────

def test_verify_chain_detects_content_edit():
    events = _chain([
        {"seq_id": 1, "tool_name": "user_prompt"},
        {"seq_id": 2, "tool_name": "Write", "args": {"path": "safe.py"}},
        {"seq_id": 3, "tool_name": "Read"},
    ])
    events[1]["args"] = {"path": "/etc/passwd"}   # edit, don't recompute
    errs = L.verify_chain(events)
    assert errs
    assert any("2" in e for e in errs)


def test_verify_chain_detects_deletion():
    events = _chain([
        {"seq_id": 1, "tool_name": "user_prompt"},
        {"seq_id": 2, "tool_name": "llm_inference"},
        {"seq_id": 3, "tool_name": "Write"},
    ])
    del events[1]                                  # drop the middle event
    errs = L.verify_chain(events)
    assert errs   # event 3's prev_hash no longer matches event 1's entry_hash


def test_verify_chain_detects_insertion():
    events = _chain([
        {"seq_id": 1, "tool_name": "user_prompt"},
        {"seq_id": 2, "tool_name": "Write"},
    ])
    forged = {"seq_id": 99, "tool_name": "Bash",
              "prev_hash": events[0]["entry_hash"]}
    forged["entry_hash"] = L.chain_hash(forged)     # self-consistent forgery
    events.insert(1, forged)                         # splice between 1 and 2
    errs = L.verify_chain(events)
    assert errs   # event 2's prev_hash still points at 1, not the forged entry


def test_verify_chain_detects_reorder():
    events = _chain([
        {"seq_id": 1, "tool_name": "a"},
        {"seq_id": 2, "tool_name": "b"},
        {"seq_id": 3, "tool_name": "c"},
    ])
    events[1], events[2] = events[2], events[1]
    assert L.verify_chain(events)


# ── HMAC: tamper-PROOF, not merely tamper-evident ───────────────────────────

def test_hmac_chain_unforgeable_without_key():
    key = b"super-secret-ledger-key"
    events = _chain([
        {"seq_id": 1, "tool_name": "user_prompt"},
        {"seq_id": 2, "tool_name": "Write", "args": {"path": "safe.py"}},
        {"seq_id": 3, "tool_name": "Read"},
    ], key=key)
    assert L.verify_chain(events, key=key) == []        # intact under the key

    # Attacker edits event 2 and re-chains the tail with plain sha256 (no key):
    events[1]["args"] = {"path": "/etc/passwd"}
    prev = events[0]["entry_hash"]
    for e in events[1:]:
        e["prev_hash"] = prev
        e["entry_hash"] = L.chain_hash(e)               # no key — best they can do
        prev = e["entry_hash"]

    # The forged chain is internally consistent but fails keyed verification.
    assert L.verify_chain(events, key=key)


def test_verify_chain_rejects_wrong_key():
    events = _chain([{"seq_id": 1, "tool_name": "x"}], key=b"right")
    assert L.verify_chain(events, key=b"wrong")


# ── LocalJournalClient writes a live chain ──────────────────────────────────

def _read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_local_journal_writes_a_verifiable_chain(tmp_path):
    jp = tmp_path / "journal.jsonl"
    c = L.LocalJournalClient(journal_path=str(jp))
    s1 = c.record_user_prompt("do the thing")
    s2 = c.record_llm_call(model="m", prompt_tokens=1, completion_tokens=2,
                           duration_ms=5, triggered_by=s1)
    c.record_tool_call("Write", {"path": "a.py"}, {"ok": True}, True, 3, triggered_by=s2)

    events = _read_events(jp)
    assert len(events) == 3
    assert all("entry_hash" in e and "prev_hash" in e for e in events)
    assert events[0]["prev_hash"] == L.GENESIS_HASH
    assert L.verify_chain(events) == []

    # Tamper a persisted line → chain breaks.
    events[1]["args"]["model"] = "evil"
    assert L.verify_chain(events)


def test_blob_dir_follows_journal_path(tmp_path, monkeypatch):
    # Blobs must live next to the journal, NOT in a cwd-relative .korg — else a
    # run with KORG_JOURNAL_PATH set still leaks content-addressed payloads into
    # the source checkout (the no_escape violation the bench caught live).
    monkeypatch.delenv("KORG_BLOB_DIR", raising=False)
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "sub" / "journal.jsonl"))
    assert L._blob_dir() == tmp_path / "sub" / "blobs"


def test_blob_dir_honors_explicit_override(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "j" / "journal.jsonl"))
    assert L._blob_dir() == Path(str(tmp_path / "b"))


def test_blob_dir_default_when_nothing_set(monkeypatch):
    monkeypatch.delenv("KORG_BLOB_DIR", raising=False)
    monkeypatch.delenv("KORG_JOURNAL_PATH", raising=False)
    assert L._blob_dir() == Path(".korg") / "blobs"


def test_local_journal_continues_chain_across_restart(tmp_path):
    jp = tmp_path / "journal.jsonl"
    c1 = L.LocalJournalClient(journal_path=str(jp))
    c1.record_user_prompt("first")
    c1.record_tool_call("Read", {"p": "x"}, {}, True, 1)

    # New client, same path — must pick up the chain head, not reset to genesis.
    c2 = L.LocalJournalClient(journal_path=str(jp))
    c2.record_tool_call("Write", {"p": "y"}, {}, True, 1)

    events = _read_events(jp)
    assert len(events) == 3
    assert events[2]["prev_hash"] == events[1]["entry_hash"]
    assert L.verify_chain(events) == []


# ── `korg verify` proof (file-level + CLI) ──────────────────────────────────

def test_verify_journal_file_runs_dag_and_chain(tmp_path):
    jp = tmp_path / "journal.jsonl"
    c = L.LocalJournalClient(journal_path=str(jp))
    s1 = c.record_user_prompt("hi")
    c.record_tool_call("Write", {"path": "a.py"}, {}, True, 1, triggered_by=s1)
    assert L.verify_journal_file(str(jp)) == []

    lines = jp.read_text().splitlines()
    obj = json.loads(lines[0]); obj["tool_name"] = "evil"
    lines[0] = json.dumps(obj)
    jp.write_text("\n".join(lines) + "\n")
    assert L.verify_journal_file(str(jp))


def test_cli_verify_command_reports_intact_then_tampered(tmp_path, monkeypatch, capsys):
    from src import cli
    jp = tmp_path / "journal.jsonl"
    c = L.LocalJournalClient(journal_path=str(jp))
    c.record_user_prompt("hi")
    c.record_tool_call("Read", {"p": "x"}, {}, True, 1)

    monkeypatch.setattr(cli.sys, "argv", ["korgex", "verify", str(jp)])
    assert cli.main() == 0
    assert "intact" in capsys.readouterr().out.lower()

    lines = jp.read_text().splitlines()
    obj = json.loads(lines[1]); obj["args"] = {"p": "evil"}
    lines[1] = json.dumps(obj)
    jp.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(cli.sys, "argv", ["korgex", "verify", str(jp)])
    assert cli.main() == 1
    assert "tamper" in capsys.readouterr().out.lower()
