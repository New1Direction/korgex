"""Tests for the witness tap that wraps any tool-dispatch loop.

Run from the korgex repo root:  python3 -m pytest integrations/witness/test_witness.py

The tap is self-contained (vendored writer, stdlib only) so the dispatcher it wraps
needs no dependency on korgex. These tests cross-verify its output against korgex's
canonical `ledger_spec` — proving the vendored writer is byte-for-byte spec-conformant
— and pin the two guarantees that matter for wrapping a production dispatcher:
pass-through and fail-safety (audit logging must never break a tool call).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # import the sandbox module
import witness  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from src import ledger_spec as S  # noqa: E402  (korgex's canonical reference)


def _read(p) -> list:
    return [json.loads(ln) for ln in open(p) if ln.strip()]


def test_vendored_chain_hash_matches_korgex_ledger_spec():
    """The vendored writer must be byte-for-byte spec-conformant with korgex."""
    event = {"schema_version": "1.0", "seq_id": 1, "source_agent": "witness",
             "tool_name": "fetch", "args": {"url": "x"}, "result": {},
             "success": True, "duration_ms": 3, "prev_hash": S.GENESIS_HASH}
    assert witness.chain_hash(event) == S.chain_hash(event)


def test_tap_records_a_verifiable_chain(tmp_path):
    out = str(tmp_path / "session.korg.jsonl")
    calls = {"n": 0}

    def handle_tool(name, arguments):
        calls["n"] += 1
        return {"ok": True, "tool": name}

    wrapped = witness.tap(handle_tool, journal_path=out)
    wrapped("fetch", {"url": "https://data.example", "limit": 100})
    wrapped("parse", {"format": "jsonl"})

    events = _read(out)
    assert calls["n"] == 2 and len(events) == 2
    assert S.verify_chain(events) == []          # the tap's chain verifies under korgex
    assert events[0]["tool_name"] == "fetch" and events[1]["tool_name"] == "parse"


def test_tap_passes_the_underlying_result_through_unchanged(tmp_path):
    sentinel = {"rows": 1280, "errors": [1, 2, 3]}
    wrapped = witness.tap(lambda n, a: sentinel, journal_path=str(tmp_path / "j.jsonl"))
    assert wrapped("parse", {"format": "csv"}) is sentinel


def test_tap_is_fail_safe_logging_never_breaks_a_tool_call(tmp_path, monkeypatch):
    out = str(tmp_path / "j.jsonl")
    wrapped = witness.tap(lambda n, a: {"ok": True}, journal_path=out)
    # Force the ledger write to explode; the tool call must still return.
    monkeypatch.setattr(witness.LedgerTap, "record",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert wrapped("fetch", {"url": "x"}) == {"ok": True}


def test_tap_is_disabled_without_a_journal_path(monkeypatch):
    monkeypatch.delenv("KORG_TAP_JOURNAL", raising=False)

    def h(n, a):
        return {"ok": True}

    assert witness.tap(h) is h  # no journal → no-op, zero overhead


def test_tap_resumes_the_chain_across_restarts(tmp_path):
    out = str(tmp_path / "session.korg.jsonl")
    witness.tap(lambda n, a: {"ok": True}, journal_path=out)("fetch", {"a": 1})
    # a fresh tap on the same journal (restart) must continue the chain
    witness.tap(lambda n, a: {"ok": True}, journal_path=out)("parse", {"a": 2})
    events = _read(out)
    assert [e["seq_id"] for e in events] == [1, 2]
    assert S.verify_chain(events) == []  # unbroken across the restart
