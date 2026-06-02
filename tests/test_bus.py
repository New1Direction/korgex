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


# ── autonomous wiring: bus tools + start-of-task auto-delivery ──
def test_bus_tools_round_trip_via_identity_env(tmp_path, monkeypatch):
    from src import tools_impl as T
    monkeypatch.setenv("KORG_BUS_JOURNAL", str(tmp_path / "bus.jsonl"))
    monkeypatch.setenv("KORG_BUS_AGENT", "alice")
    assert T.tool_bus_send("bob", "ping")["sent"] is True
    monkeypatch.setenv("KORG_BUS_AGENT", "bob")
    inb = T.tool_bus_inbox()
    assert [m["body"] for m in inb["messages"]] == ["ping"]
    assert T.tool_bus_inbox()["messages"] == []  # marked read on view


def test_bus_tools_error_without_identity(monkeypatch):
    from src import tools_impl as T
    monkeypatch.delenv("KORG_BUS_JOURNAL", raising=False)
    monkeypatch.delenv("KORG_BUS_AGENT", raising=False)
    assert "error" in T.tool_bus_send("bob", "hi")
    assert "error" in T.tool_bus_inbox()


def test_agent_auto_delivers_pending_messages_into_the_prompt(tmp_path, monkeypatch):
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "peer", "alice", "the PR is ready for your review")
    monkeypatch.setenv("KORG_BUS_JOURNAL", j)
    monkeypatch.setenv("KORG_BUS_AGENT", "alice")
    from src.agent import KorgexAgent

    class FakeLedger:
        def __init__(self):
            self.events = []

        def record_tool_call(self, **k):
            self.events.append(k)
            return 1

    a = KorgexAgent(repo_root=str(tmp_path))
    msgs = [{"role": "user", "content": "do the task"}]
    led = FakeLedger()
    a._bus_deliver_initial(msgs, led, 1)
    assert "the PR is ready for your review" in msgs[0]["content"]  # injected into the prompt
    assert led.events[-1]["tool_name"] == "bus.deliver"
    assert bus.inbox(j, "alice") == []  # delivered → marked read
