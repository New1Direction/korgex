"""
import_adapters.py — replay other vendors' agent sessions into a korg-ledger@v1
chained journal (roadmap idea #7).

Competitors' agent memory is their lock-in; "cross-vendor" is the wedge they
structurally can't ship. These adapters ingest a vendor transcript (Claude Code
JSONL to start) and re-emit it as korg-ledger@v1 chained events — one
inspectable, verifiable local artifact spanning vendors. The output verifies
under the same `ledger_spec` the whole ecosystem shares, so korg becomes the
neutral audit substrate that sits UNDER every agent, not another agent beside
them.

A vendor adapter is just `parse_<vendor>(lines) -> [action]`, where an action is
{op, payload, uuid, parent_uuid}. `to_ledger_events` does the vendor-agnostic
work: assign monotonic seq_ids, reconstruct triggered_by from parent pointers,
and hash-chain via the shared spec.
"""

from __future__ import annotations

import json
import os

from src import ledger_spec as S

SCHEMA_VERSION = "1.0"


# ── Claude Code adapter ─────────────────────────────────────────────────────

def _text_of(content) -> str:
    """Flatten a Claude Code message `content` (str or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", "thinking"):
                parts.append(b.get("text") or b.get("thinking") or "")
        return "\n".join(p for p in parts if p)
    return ""


def parse_claude_code(lines: list) -> list:
    """Normalize Claude Code transcript events into ordered ledger actions.

    Each action = {op, payload, uuid, parent_uuid}. A user line → one
    `user_prompt`; an assistant line → one `llm_inference` plus a `tool_call`
    per tool_use block (parented to that assistant turn). Metadata/system/
    tool_result-only lines are skipped.
    """
    actions: list = []
    for e in lines:
        if not isinstance(e, dict):
            continue
        etype = e.get("type")
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        uuid = e.get("uuid")
        parent = e.get("parentUuid")

        if etype == "user" and not e.get("isMeta"):
            text = _text_of(msg.get("content"))
            if text.strip():  # skip tool-result-only user turns (no prose)
                actions.append({"op": "user_prompt", "uuid": uuid, "parent_uuid": parent,
                                "payload": {"prompt": text}})
        elif etype == "assistant":
            content = msg.get("content")
            text = _text_of(content)
            actions.append({"op": "llm_inference", "uuid": uuid, "parent_uuid": parent,
                            "payload": {"model": msg.get("model", "unknown"), "text": text}})
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    actions.append({
                        "op": "tool_call", "uuid": b.get("id"), "parent_uuid": uuid,
                        "payload": {"tool_name": b.get("name", "tool"), "args": b.get("input") or {}},
                    })
    return actions


def discover_claude_code_sessions(root: str | None = None) -> list:
    """Find Claude Code session transcripts (newest first).

    Defaults to `~/.claude/projects/**/*.jsonl` — the logs a Claude Code user
    already has. Returns absolute paths sorted by mtime (most recent first), so
    `korgex audit` can grab the latest session with zero configuration.
    """
    import glob
    base = root or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    paths = [p for p in glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True)
             if os.path.isfile(p)]
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths


ADAPTERS = {
    "claude-code": parse_claude_code,
}

_SOURCE_AGENTS = {
    "claude-code": "claude-code",
}


# ── vendor-agnostic: actions → chained ledger events ────────────────────────

def to_ledger_events(actions: list, source_agent: str) -> list:
    """Assign seq_ids, reconstruct triggered_by from parent pointers, and
    hash-chain the events per korg-ledger@v1. Returns the chained event dicts."""
    events: list = []
    uuid_to_seq: dict = {}
    prev_hash = S.GENESIS_HASH
    seq = 0
    for a in actions:
        seq += 1
        op = a["op"]
        payload = a["payload"]
        # triggered_by: the seq of the action whose uuid is our parent (always
        # earlier in transcript order → strictly-earlier DAG invariant holds).
        parent = a.get("parent_uuid")
        triggered_by = uuid_to_seq.get(parent) if parent is not None else None
        if triggered_by is not None and triggered_by >= seq:
            triggered_by = None  # defensive: never violate strictly-earlier

        if op == "user_prompt":
            tool_name, args, result = "user_prompt", {"prompt": payload["prompt"]}, {}
        elif op == "llm_inference":
            tool_name = "llm_inference"
            args = {"model": payload.get("model", "unknown")}
            result = {"text": payload.get("text", "")}
        else:  # tool_call → the tool's own name, like a native korgex tool event
            tool_name = payload.get("tool_name", "tool")
            args, result = payload.get("args", {}), {}

        event = {
            "schema_version": SCHEMA_VERSION,
            "seq_id": seq,
            "source_agent": source_agent,
            "tool_name": tool_name,
            "args": args,
            "result": result,
            "success": True,
            "duration_ms": 0,
        }
        if triggered_by is not None:
            event["triggered_by"] = triggered_by
        event["prev_hash"] = prev_hash
        event["entry_hash"] = S.chain_hash(event)
        prev_hash = event["entry_hash"]

        if a.get("uuid") is not None:
            uuid_to_seq.setdefault(a["uuid"], seq)
        events.append(event)
    return events


def import_transcript(path: str, vendor: str, out_path: str,
                      source_agent: str | None = None) -> dict:
    """Read a vendor transcript, re-emit it as a korg-ledger@v1 chained JSONL
    journal at `out_path`, and return a summary. Raises ValueError on unknown
    vendor. The output is verifiable by ledger_spec.verify_chain/verify_dag and
    `korgex verify`."""
    parser = ADAPTERS.get(vendor)
    if parser is None:
        raise ValueError(f"unknown vendor {vendor!r}; known: {sorted(ADAPTERS)}")
    source_agent = source_agent or _SOURCE_AGENTS.get(vendor, vendor)

    lines = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue

    events = to_ledger_events(parser(lines), source_agent=source_agent)

    import os
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    with open(out_path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    errors = S.verify_chain(events) + S.verify_dag(events)
    return {
        "vendor": vendor,
        "source_agent": source_agent,
        "events": len(events),
        "out_path": out_path,
        "verified": not errors,
        "errors": errors,
    }
