"""
mcp_server.py — expose korg-ledger@v1 as MCP tools (the substrate, over MCP).

"Be the substrate, not an app" at the protocol layer. A dependency-free
JSON-RPC 2.0 stdio MCP server so ANY MCP host can
reach korg's verifiable-cognition capability:

  • korg_verify  — prove a journal is tamper-evident-intact (hash-chain + DAG)
  • korg_audit   — audit the host agent's OWN Claude Code logs (import + verify)
  • korg_import  — import a vendor transcript into a verifiable korg-ledger journal

This is the governance/audit gap the MCP 2026 roadmap names: a tool any agent
can call to make its own cognition checkable. `handle_request(req) -> resp|None`
is pure (testable without a host); `serve()` runs the stdio loop.

Run for a host:  python3 -m src.mcp_server   (or `korgex mcp-server`)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version

from src import import_adapters as IA
from src import ledger_spec as S

PROTOCOL_VERSION = "2024-11-05"
try:
    _PACKAGE_VERSION = version("korgex")
except PackageNotFoundError:  # pragma: no cover - source tree without installed dist metadata
    _PACKAGE_VERSION = "0.0.0+dev"
SERVER_INFO = {"name": "korg-ledger", "version": _PACKAGE_VERSION}


def _read_jsonl(path: str) -> list:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _text(s: str, is_error: bool = False) -> dict:
    """An MCP tools/call result: a text content block + isError flag."""
    return {"content": [{"type": "text", "text": s}], "isError": is_error}


# ── tool handlers ───────────────────────────────────────────────────────────

def _tool_verify(args: dict) -> dict:
    path = args.get("journal_path")
    if not path:
        return _text("journal_path is required", True)
    key = args.get("hmac_key")
    key = key.encode() if key else None
    try:
        events = _read_jsonl(path)
    except (OSError, ValueError) as exc:
        return _text(f"cannot read journal: {exc}", True)
    errors = S.verify_chain(events, key) + S.verify_dag(events)
    if not errors:
        return _text(f"✓ INTACT — {len(events)} events, hash-chain verified ({path})")
    return _text(f"✗ TAMPERED — {len(errors)} problem(s): " + "; ".join(errors[:5]), True)


def _tool_import(args: dict) -> dict:
    transcript = args.get("transcript")
    if not transcript:
        return _text("transcript is required", True)
    vendor = args.get("vendor", "claude-code")
    out = args.get("out") or (transcript.rsplit(".", 1)[0] + ".korg.jsonl")
    try:
        summary = IA.import_transcript(transcript, vendor=vendor, out_path=out)
    except (ValueError, OSError) as exc:
        return _text(f"import failed: {exc}", True)
    verdict = "✓ verified intact" if summary["verified"] else f"✗ {summary['errors'][:3]}"
    return _text(f"imported {summary['events']} events from '{vendor}' → "
                 f"{summary['out_path']}  [{verdict}]", not summary["verified"])


def _tool_audit(args: dict) -> dict:
    found = IA.discover_claude_code_sessions(root=args.get("root"))
    session = args.get("session") or (found[0] if found else None)
    if not session:
        return _text("no Claude Code sessions found under ~/.claude/projects", True)
    out = args.get("out") or os.path.join(tempfile.gettempdir(), "korg-mcp-audit.jsonl")
    try:
        summary = IA.import_transcript(session, vendor="claude-code", out_path=out)
    except (ValueError, OSError) as exc:
        return _text(f"audit failed: {exc}", True)
    if summary["verified"]:
        return _text(f"audited {os.path.basename(session)} → {summary['events']} events; "
                     f"chain: ✓ INTACT — tamper-evident; journal: {summary['out_path']}")
    return _text(f"audited {os.path.basename(session)} → {summary['events']} events; "
                 f"chain: ✗ TAMPERED {summary['errors'][:3]}", True)


def _bus_journal(args: dict) -> str:
    return (args.get("journal") or os.environ.get("KORG_BUS_JOURNAL")
            or os.path.join(os.path.expanduser("~"), ".korg", "bus.jsonl"))


def _tool_web_search(args: dict) -> dict:
    from src import web_tools as W
    q = args.get("query", "")
    res = W.tool_web_search(q, max_results=int(args.get("max_results", 5) or 5))
    if "error" in res:
        return _text(res["error"], True)
    rows = [f"- {r['title']} — {r['url']}\n  {r['snippet']}" for r in res.get("results", [])]
    return _text((f"results for {q!r}:\n" + "\n".join(rows)) if rows else f"no results for {q!r}")


def _tool_web_fetch(args: dict) -> dict:
    from src import web_tools as W
    res = W.tool_web_fetch(args.get("url", ""), max_chars=int(args.get("max_chars", 20000) or 20000))
    if "error" in res:
        return _text(res["error"], True)
    return _text(f"# {res.get('title', '')}\n\n{res.get('text', '')}")


def _tool_bus_send(args: dict) -> dict:
    from src import bus as B
    to = args.get("to")
    if not to:
        return _text("'to' is required", True)
    seq = B.send(_bus_journal(args), args.get("from", "host"), to, args.get("message", ""))
    return _text(f"sent #{seq}: {args.get('from', 'host')} → {to} (chained + verifiable)")


def _tool_bus_inbox(args: dict) -> dict:
    from src import bus as B
    agent = args.get("agent")
    if not agent:
        return _text("'agent' is required", True)
    journal = _bus_journal(args)
    msgs = B.inbox(journal, agent)
    if not msgs:
        return _text(f"no unread messages for {agent}")
    out = "\n".join(f"#{m['seq']} {m['from']} → {m['to']}: {m['body']}" for m in msgs)
    B.mark_read(journal, agent, [m["seq"] for m in msgs])
    return _text(out)


TOOLS = {
    "web_search": {
        "description": "Search the web (DuckDuckGo, no key) — returns titles, URLs, snippets.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "the search query"},
            "max_results": {"type": "integer", "description": "max results (default 5)"}},
            "required": ["query"]},
        "handler": _tool_web_search,
    },
    "web_fetch": {
        "description": "Fetch an http(s) URL and return its readable text + title.",
        "inputSchema": {"type": "object", "properties": {
            "url": {"type": "string", "description": "the http(s) URL to fetch"},
            "max_chars": {"type": "integer", "description": "max characters (default 20000)"}},
            "required": ["url"]},
        "handler": _tool_web_fetch,
    },
    "bus_send": {
        "description": "Send a message to another agent over the verifiable, tamper-evident korg bus.",
        "inputSchema": {"type": "object", "properties": {
            "to": {"type": "string", "description": "recipient agent name"},
            "message": {"type": "string", "description": "message body"},
            "from": {"type": "string", "description": "sender name (default: host)"},
            "journal": {"type": "string", "description": "bus journal path (default: ~/.korg/bus.jsonl)"}},
            "required": ["to", "message"]},
        "handler": _tool_bus_send,
    },
    "bus_inbox": {
        "description": "Read (and mark read) unread bus messages addressed to an agent.",
        "inputSchema": {"type": "object", "properties": {
            "agent": {"type": "string", "description": "the agent whose inbox to read"},
            "journal": {"type": "string", "description": "bus journal path (default: ~/.korg/bus.jsonl)"}},
            "required": ["agent"]},
        "handler": _tool_bus_inbox,
    },
    "korg_verify": {
        "description": "Verify a korg-ledger@v1 journal is intact — proves the recorded run "
                       "was not edited, deleted, reordered, or spliced (hash-chain + causal DAG).",
        "inputSchema": {"type": "object", "properties": {
            "journal_path": {"type": "string", "description": "path to the JSONL ledger journal"},
            "hmac_key": {"type": "string", "description": "optional HMAC key for tamper-proof chains"}},
            "required": ["journal_path"]},
        "handler": _tool_verify,
    },
    "korg_import": {
        "description": "Import another vendor's session transcript (claude-code) into a "
                       "verifiable korg-ledger@v1 chained journal.",
        "inputSchema": {"type": "object", "properties": {
            "transcript": {"type": "string", "description": "path to the vendor session transcript"},
            "vendor": {"type": "string", "description": "claude-code"},
            "out": {"type": "string", "description": "output journal path"}},
            "required": ["transcript"]},
        "handler": _tool_import,
    },
    "korg_audit": {
        "description": "Audit the agent's own Claude Code logs: import the latest session into a "
                       "verifiable ledger and report its tamper-status. Zero-config.",
        "inputSchema": {"type": "object", "properties": {
            "session": {"type": "string", "description": "a specific transcript (default: newest)"},
            "root": {"type": "string", "description": "sessions root (default: ~/.claude/projects)"},
            "out": {"type": "string", "description": "output journal path"}}},
        "handler": _tool_audit,
    },
}


def handle_request(req: dict):
    """Handle one JSON-RPC request. Returns a response dict, or None for a
    notification (no `id`). Pure — no I/O beyond what the tool handlers do."""
    method = req.get("method")
    rid = req.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL_VERSION,
                   "serverInfo": SERVER_INFO,
                   "capabilities": {"tools": {}}})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": t["description"],
                              "inputSchema": t["inputSchema"]} for n, t in TOOLS.items()]})
    if method == "tools/call":
        params = req.get("params") or {}
        tool = TOOLS.get(params.get("name"))
        if not tool:
            return err(-32602, f"unknown tool: {params.get('name')}")
        try:
            return ok(tool["handler"](params.get("arguments") or {}))
        except Exception as exc:  # surface tool faults as a tool error, not a crash
            return ok(_text(f"tool error: {exc}", True))
    if rid is None:
        return None  # notification — no response
    return err(-32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> None:  # pragma: no cover — the stdio loop
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        resp = handle_request(req)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    serve()
