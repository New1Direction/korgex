"""ACP (Agent Client Protocol) — korgex as an ACP *agent*: JSON-RPC 2.0 over stdio so
editors/clients (Zed et al.) can drive it. Implemented clean-room from the open spec
(agentclientprotocol.com): client→agent initialize / session.new / session.prompt /
session.cancel; agent→client session/update notifications (discriminator `sessionUpdate`,
content blocks keyed by `type`); stop reasons end_turn|max_tokens|cancelled|refusal.

The protocol layer is unit-tested here; end-to-end against a real ACP client is the
remaining validation (noted in src/acp.py).
"""
import io
import json

from src import acp


def _req(mid, method, params=None):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}


def test_initialize_returns_capabilities():
    resp = acp.AcpAgent().handle(_req(1, "initialize", {
        "protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": True}}}))
    assert resp["id"] == 1
    r = resp["result"]
    assert r["protocolVersion"] == 1
    assert "agentCapabilities" in r and "authMethods" in r


def test_session_new_returns_a_session_id_and_tracks_it():
    a = acp.AcpAgent()
    sid = a.handle(_req(2, "session/new", {"cwd": "/repo", "mcpServers": []}))["result"]["sessionId"]
    assert sid and sid in a.sessions


def test_session_prompt_streams_a_chunk_and_returns_stop_reason():
    sent = []
    a = acp.AcpAgent(run_turn=lambda text, sess: {"text": f"did: {text}", "stop_reason": "end_turn"},
                     send=sent.append)
    sid = a.handle(_req(1, "session/new", {"cwd": "/r"}))["result"]["sessionId"]
    resp = a.handle(_req(2, "session/prompt",
                         {"sessionId": sid, "prompt": [{"type": "text", "text": "fix the bug"}]}))
    assert resp["result"]["stopReason"] == "end_turn"
    upd = [m for m in sent if m.get("method") == "session/update"]
    assert upd and upd[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert "did: fix the bug" in upd[0]["params"]["update"]["content"]["text"]
    assert upd[0]["params"]["update"]["content"]["type"] == "text"


def test_session_prompt_unknown_session_is_an_error():
    resp = acp.AcpAgent().handle(_req(3, "session/prompt", {"sessionId": "nope", "prompt": []}))
    assert "error" in resp and resp["error"]["code"] == -32602


def test_unknown_method_errors_but_notification_is_silent():
    a = acp.AcpAgent()
    assert a.handle(_req(4, "bogus/method"))["error"]["code"] == -32601      # request → error
    # a notification (no id) never gets a response, even cancel for an unknown session
    assert a.handle({"jsonrpc": "2.0", "method": "session/cancel",
                     "params": {"sessionId": "x"}}) is None


def test_serve_loop_over_stdio_roundtrips():
    instream = io.StringIO(
        json.dumps(_req(1, "initialize", {"protocolVersion": 1})) + "\n"
        + json.dumps(_req(2, "session/new", {"cwd": "/r"})) + "\n")
    out = io.StringIO()
    acp.serve(acp.AcpAgent(), instream=instream, outstream=out)
    lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    assert lines[0]["id"] == 1 and "result" in lines[0]
    assert lines[1]["id"] == 2 and lines[1]["result"]["sessionId"]
