"""A verifiable agent message bus on korg-ledger@v1.

Agents coordinate by messaging each other — and every message **and every read
receipt** is a hash-chained, tamper-evident, causally-ordered ledger event. So you
can *prove* what your agents told each other, in what order, and that nothing was
altered — then render the whole exchange as a self-verifying `audit --html`.

Multi-agent frameworks have messaging; this one is **auditable**. It reuses the
journal korgex already writes, so:
  * secrets in message bodies are redacted at the ledger boundary (src/sanitize),
  * the chain `verify`s, and the conversation `audit`s,
  * any agent (korgex, Claude Code, Codex, …) that appends to the shared journal
    joins the bus — cross-vendor by construction.

The chain is append-only, so a message is never mutated: "read" is recorded as a
separate `message_read` event, which makes read receipts provable too. The journal
client resumes the chain from disk on every open, so concurrent senders each append
off the current tip.
"""
from __future__ import annotations

import json
import os

from src.korg_ledger import LocalJournalClient

MESSAGE = "agent_message"
READ = "message_read"
_SOURCE = "korg:bus"


def _client(journal_path: str) -> LocalJournalClient:
    return LocalJournalClient(journal_path=journal_path, source_agent=_SOURCE)


def _events(journal_path: str) -> list:
    if not os.path.exists(journal_path):
        return []
    with open(journal_path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _as_msg(e: dict) -> dict:
    a = e.get("args") or {}
    return {"seq": e.get("seq_id"), "from": a.get("from"), "to": a.get("to"),
            "team": a.get("team"), "body": a.get("body"), "in_reply_to": e.get("triggered_by")}


def _all_messages(events: list) -> list:
    return [_as_msg(e) for e in events if e.get("tool_name") == MESSAGE]


def _read_seqs(events: list, agent: str) -> set:
    return {(e.get("args") or {}).get("message_seq")
            for e in events
            if e.get("tool_name") == READ and (e.get("args") or {}).get("agent") == agent}


def send(journal_path: str, frm: str, to: str, body: str, *,
         team: str | None = None, in_reply_to: int | None = None) -> int:
    """Append a message as a chained ledger event. Returns its seq_id. The body is
    redacted of secrets and hash-linked into the tamper-evident chain on write."""
    return _client(journal_path).record_tool_call(
        MESSAGE, {"from": frm, "to": to, "team": team, "body": body}, {},
        True, 0, triggered_by=in_reply_to)


def inbox(journal_path: str, agent: str) -> list:
    """Unread messages addressed to `agent` (those without a read receipt yet)."""
    events = _events(journal_path)
    read = _read_seqs(events, agent)
    return [m for m in _all_messages(events) if m["to"] == agent and m["seq"] not in read]


def mark_read(journal_path: str, agent: str, seqs: list) -> list:
    """Record a read receipt per message — an appended chained event (never a mutation)."""
    c = _client(journal_path)
    return [c.record_tool_call(READ, {"agent": agent, "message_seq": s}, {}, True, 0) for s in seqs]


def history(journal_path: str, *, agent: str | None = None, team: str | None = None) -> list:
    """All messages (chronological), optionally filtered to an agent or a team."""
    out = _all_messages(_events(journal_path))
    if agent is not None:
        out = [m for m in out if agent in (m["from"], m["to"])]
    if team is not None:
        out = [m for m in out if m["team"] == team]
    return out


def members(journal_path: str) -> list:
    """Distinct agents that have sent or received on the bus."""
    s: set = set()
    for m in _all_messages(_events(journal_path)):
        s.add(m["from"])
        s.add(m["to"])
    return sorted(x for x in s if x)
