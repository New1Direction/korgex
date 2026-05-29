"""
korg-ledger MCP server — expose the verifiable-cognition substrate over MCP.

"Be the substrate, not an app" at the protocol layer: any MCP host (Claude
Desktop, Cursor, …) can call korg to verify a journal, audit its own session
logs, or import a vendor transcript into a chained ledger — the governance/audit
gap the MCP roadmap names. Dependency-free JSON-RPC 2.0 over stdio; the protocol
handler is pure (`handle_request(req) -> resp|None`) so it tests without a host.
"""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import mcp_server as M  # noqa: E402
from src import import_adapters as IA  # noqa: E402


def _req(method, params=None, rid=1):
    r = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        r["id"] = rid
    if params is not None:
        r["params"] = params
    return r


def test_initialize_handshake():
    resp = M.handle_request(_req("initialize"))
    assert resp["id"] == 1
    r = resp["result"]
    assert r["serverInfo"]["name"] == "korg-ledger"
    assert "protocolVersion" in r and "tools" in r["capabilities"]


def test_tools_list_exposes_the_korg_tools():
    resp = M.handle_request(_req("tools/list"))
    names = {t["name"] for t in resp["result"]["tools"]}
    assert {"korg_verify", "korg_import", "korg_audit"} <= names
    for t in resp["result"]["tools"]:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_notification_gets_no_response():
    # a JSON-RPC notification (no id), e.g. notifications/initialized
    assert M.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_jsonrpc_error():
    resp = M.handle_request(_req("does/notexist"))
    assert resp["error"]["code"] == -32601


def test_unknown_tool_errors():
    resp = M.handle_request(_req("tools/call", {"name": "nope", "arguments": {}}))
    assert resp["error"]["code"] == -32602


def _make_journal(tmp_path):
    src = tmp_path / "s.jsonl"
    src.write_text("\n".join(json.dumps(l) for l in [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "fix it"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"f": "x.py"}}]}},
    ]) + "\n")
    out = tmp_path / "j.jsonl"
    IA.import_transcript(str(src), vendor="claude-code", out_path=str(out))
    return out


def test_tools_call_verify_intact(tmp_path):
    out = _make_journal(tmp_path)
    resp = M.handle_request(_req("tools/call",
                                 {"name": "korg_verify", "arguments": {"journal_path": str(out)}}))
    res = resp["result"]
    assert res["isError"] is False
    assert "INTACT" in res["content"][0]["text"]


def test_tools_call_verify_detects_tamper(tmp_path):
    out = _make_journal(tmp_path)
    events = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    events[0]["args"] = {"prompt": "EVIL"}            # tamper without re-hashing
    out.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    resp = M.handle_request(_req("tools/call",
                                 {"name": "korg_verify", "arguments": {"journal_path": str(out)}}))
    res = resp["result"]
    assert res["isError"] is True
    assert "TAMPER" in res["content"][0]["text"].upper()


def test_tools_call_audit(tmp_path):
    base = tmp_path / "projects" / "p"
    base.mkdir(parents=True)
    (base / "s.jsonl").write_text(json.dumps(
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "hello"}}) + "\n")
    resp = M.handle_request(_req("tools/call", {"name": "korg_audit", "arguments": {
        "root": str(tmp_path / "projects"), "out": str(tmp_path / "audit.jsonl")}}))
    res = resp["result"]
    assert res["isError"] is False
    assert "INTACT" in res["content"][0]["text"]
