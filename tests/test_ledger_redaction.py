"""Secrets are redacted at the ledger-write boundary (so shareable proofs are safe).

Redaction must happen BEFORE the event is hash-chained — so the chain still
verifies AND the secret never appears in the journal (or its blob store).
"""
from __future__ import annotations

import json

from src import ledger_spec as S
from src.korg_ledger import LocalJournalClient


def _events(path):
    return [json.loads(ln) for ln in open(path) if ln.strip()]


def test_local_journal_redacts_a_secret_in_tool_args(tmp_path):
    jp = str(tmp_path / "j.jsonl")
    c = LocalJournalClient(journal_path=jp)
    c.record_tool_call(
        "Bash",
        {"command": "curl -H 'Authorization: Bearer sk-or-v1-0123456789abcdef0123456789abcdef'"},
        {"ok": True}, True, 5,
    )
    blob = json.dumps(_events(jp)[-1])
    assert "sk-or-v1" not in blob and "[REDACTED]" in blob


def test_local_journal_redacts_a_secret_named_result_field(tmp_path):
    jp = str(tmp_path / "j.jsonl")
    c = LocalJournalClient(journal_path=jp)
    c.record_tool_call("Write", {"file_path": ".env"}, {"api_key": "abc123", "ok": True}, True, 1)
    ev = _events(jp)[-1]
    assert ev["result"]["api_key"] == "[REDACTED]" and ev["result"]["ok"] is True


def test_redaction_happens_before_hashing_so_the_chain_still_verifies(tmp_path):
    jp = str(tmp_path / "j.jsonl")
    c = LocalJournalClient(journal_path=jp)
    c.record_user_prompt("here is my key ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 thanks")
    c.record_tool_call("Read", {"file_path": "x"}, {"text": "hi"}, True, 1)
    events = _events(jp)
    assert S.verify_chain(events) == []          # chain intact (hashed over redacted content)
    assert "ghp_ABCDEF" not in open(jp).read()   # the secret never reached disk
