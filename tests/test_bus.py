"""A verifiable agent message bus on korg-ledger@v1.

Agents message each other — and every message (and every read receipt) is a
hash-chained, tamper-evident, causally-ordered ledger event. So you can PROVE what
agents told each other, in what order, unaltered — and render it as a self-verifying
audit. Read state is itself an appended event (the chain is immutable: you never
mutate a message, you record that you read it). Secrets in bodies are redacted.
"""
from __future__ import annotations

import json

from src import bus
from src import ledger_spec as S


def _events(p):
    return [json.loads(ln) for ln in open(p) if ln.strip()]


def test_send_appends_a_verifiable_chained_message(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "alice", "bob", "ship it")
    ev = _events(j)
    assert S.verify_chain(ev) == []  # message is on the tamper-evident chain
    m = [e for e in ev if e["tool_name"] == "agent_message"][0]["args"]
    assert m["from"] == "alice" and m["to"] == "bob" and m["body"] == "ship it"


def test_inbox_returns_unread_then_empty_after_mark_read(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "alice", "bob", "review pls")
    inb = bus.inbox(j, "bob")
    assert len(inb) == 1 and inb[0]["body"] == "review pls" and inb[0]["from"] == "alice"
    bus.mark_read(j, "bob", [inb[0]["seq"]])
    assert bus.inbox(j, "bob") == []            # read → no longer unread
    assert S.verify_chain(_events(j)) == []      # read receipt is itself a valid chained event


def test_two_agents_each_see_only_their_own_inbox(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "alice", "bob", "hi bob")
    bus.send(j, "bob", "alice", "hi alice")
    assert [m["body"] for m in bus.inbox(j, "bob")] == ["hi bob"]
    assert [m["body"] for m in bus.inbox(j, "alice")] == ["hi alice"]


def test_secret_in_a_message_body_is_redacted(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "alice", "bob", "the key is sk-or-v1-0123456789abcdef0123456789abcdef ok")
    raw = open(j).read()
    assert "sk-or-v1" not in raw and "[REDACTED]" in raw


def test_tampering_a_message_breaks_the_chain(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "alice", "bob", "original")
    ev = _events(j)
    ev[0]["args"]["body"] = "forged"
    assert S.verify_chain(ev)  # tamper detected (non-empty error list)


def test_reply_threading_links_causally(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    s1 = bus.send(j, "alice", "bob", "question?")
    bus.send(j, "bob", "alice", "answer", in_reply_to=s1)
    reply = [m for m in bus.history(j) if m["body"] == "answer"][0]
    assert reply["in_reply_to"] == s1


def test_members_and_history_filtering(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "alice", "bob", "a")
    bus.send(j, "bob", "carol", "b")
    assert set(bus.members(j)) == {"alice", "bob", "carol"}
    assert len(bus.history(j)) == 2
    assert [m["body"] for m in bus.history(j, agent="carol")] == ["b"]
