"""
Durable-ledger tests (Gate D — no silent no-op).

Today record_user_prompt returns None when the korg server is down, so root_seq
is None and the causal DAG silently doesn't exist — fatal for audit/rewind of an
unattended self-coding run. Gate D adds a local append-only JSONL journal that
ALWAYS persists with real, monotonic seq_ids, and makes the default-client
resolver fall back to it (never to a no-op). The journal is recall-readable.
"""

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import korg_ledger as L  # noqa: E402
from src.recall import load_events  # noqa: E402


def test_local_journal_persists_real_seqs_and_is_recall_readable(tmp_path):
    p = str(tmp_path / "journal.jsonl")
    c = L.LocalJournalClient(journal_path=p)

    root = c.record_user_prompt("add a feature")
    assert root == 1                                   # never None
    s2 = c.record_llm_call(model="m", prompt_tokens=0, completion_tokens=0,
                           duration_ms=0, triggered_by=root)
    assert s2 == 2
    s3 = c.record_tool_call(tool_name="Write", args={"file_path": "x.py"},
                            result={"ok": True}, success=True, duration_ms=1, triggered_by=s2)
    assert s3 == 3

    events = load_events(p)                            # the recall reader parses it
    assert [e["seq_id"] for e in events] == [1, 2, 3]
    assert events[1]["triggered_by"] == 1              # causal edge persisted


def test_local_journal_is_durable_across_restart(tmp_path):
    p = str(tmp_path / "journal.jsonl")
    L.LocalJournalClient(journal_path=p).record_user_prompt("first")
    # a fresh client continues the seq counter from the file — survives restarts
    c2 = L.LocalJournalClient(journal_path=p)
    assert c2.record_user_prompt("second") == 2


def test_default_client_falls_back_to_local_not_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(L, "_default_client", None)
    # bridge unimportable AND no korg server reachable
    def _no_bridge(*a, **k):
        raise ImportError("korg_bridge not built")
    monkeypatch.setattr(L, "KorgBridgeClient", _no_bridge)
    monkeypatch.setattr(L.KorgLedgerClient, "_is_available", lambda self: False)

    c = L.get_default_client()
    assert isinstance(c, L.LocalJournalClient)         # durable fallback, not a no-op
    assert c.record_user_prompt("x") == 1              # and it actually records
    monkeypatch.setattr(L, "_default_client", None)


def test_forced_local_ledger_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("KORGEX_LEDGER", "local")
    monkeypatch.setattr(L, "_default_client", None)
    assert isinstance(L.get_default_client(), L.LocalJournalClient)
    monkeypatch.setattr(L, "_default_client", None)
