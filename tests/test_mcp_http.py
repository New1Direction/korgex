"""Remote (HTTP) transport for the MCP client.

korgex could only spawn stdio subprocess servers. This adds talking JSON-RPC to a
remote server over HTTP POST — the "cool recent" remote-MCP shape (url + auth
header). The HTTP poster is injected so the whole thing tests offline, including
the case where the server replies with a text/event-stream (SSE) body.
"""
import json

from src.mcp_client import MCPClient, MCPServerConfig


def make_http_post(tools=None, call_result=None):
    """A fake HTTP poster: post(url, payload, headers, timeout) -> (status, body)."""
    calls = []

    def post(url, payload, headers, timeout):
        calls.append((url, payload, headers))
        m = payload.get("method")
        rid = payload.get("id")
        if m == "initialize":
            res = {"capabilities": {"tools": {}}}
        elif m == "tools/list":
            res = {"tools": tools or []}
        elif m == "tools/call":
            res = call_result if call_result is not None else {}
        else:
            res = {}
        return 200, json.dumps({"jsonrpc": "2.0", "id": rid, "result": res})
    return post, calls


def test_http_client_echoes_the_mcp_session_id():
    # Streamable-HTTP servers (Context7 et al.) issue an `mcp-session-id` on initialize
    # and REQUIRE it echoed on every later request, or tools/list comes back empty.
    # Found live: korgex connected to Context7 but discovered 0 tools without this.
    calls = []

    def post(url, payload, headers, timeout):
        m, rid = payload.get("method"), payload.get("id")
        calls.append((m, dict(headers)))
        if m == "initialize":
            res = {"capabilities": {"tools": {}}}
        elif m == "tools/list":
            res = {"tools": [{"name": "doc", "description": "d", "inputSchema": {}}]}
        else:
            res = {}
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "result": res})
        # the server hands out the session id ONLY on initialize (case-insensitive)
        resp_headers = {"mcp-session-id": "sess-xyz"} if m == "initialize" else {}
        return 200, body, resp_headers

    c = MCPClient(MCPServerConfig(name="r", transport="http", url="https://x"), http_post=post)
    assert c.connect()["status"] == "connected"
    tools = c.discover_tools()
    assert [t.name for t in tools] == ["doc"]            # tools actually came back
    by_method = dict(calls)
    assert "Mcp-Session-Id" not in by_method["initialize"]   # none issued yet
    assert by_method["tools/list"]["Mcp-Session-Id"] == "sess-xyz"  # echoed after


def test_http_client_connects_and_discovers_tools():
    post, calls = make_http_post(tools=[{"name": "search", "description": "d", "inputSchema": {}}])
    cfg = MCPServerConfig(name="remote", transport="http", url="https://mcp.x/api",
                          headers={"Authorization": "Bearer t"})
    c = MCPClient(cfg, http_post=post)

    assert c.connect()["status"] == "connected"
    tools = c.discover_tools()
    assert [t.name for t in tools] == ["search"]
    assert tools[0].server_name == "remote"
    # the auth header rode along on the request
    assert calls[0][2].get("Authorization") == "Bearer t"


def test_http_client_calls_tool_with_name_and_args():
    post, calls = make_http_post(call_result={"content": [{"type": "text", "text": "hi"}]})
    c = MCPClient(MCPServerConfig(name="r", transport="http", url="https://x"), http_post=post)
    c.connect()

    out = c.call_tool("search", {"q": "x"})

    assert out == {"content": [{"type": "text", "text": "hi"}]}
    payloads = [p for (_, p, _) in calls if p.get("method") == "tools/call"]
    assert payloads[0]["params"] == {"name": "search", "arguments": {"q": "x"}}


def test_http_client_parses_sse_event_stream_body():
    # Streamable-HTTP servers may answer with text/event-stream; extract the JSON-RPC result.
    def post(url, payload, headers, timeout):
        body = {"jsonrpc": "2.0", "id": payload.get("id")}
        if payload["method"] == "tools/list":
            body["result"] = {"tools": [{"name": "t", "description": "", "inputSchema": {}}]}
        else:
            body["result"] = {"capabilities": {}}
        return 200, f"event: message\ndata: {json.dumps(body)}\n\n"

    c = MCPClient(MCPServerConfig(name="r", transport="http", url="https://x"), http_post=post)
    assert c.connect()["status"] == "connected"
    assert [t.name for t in c.discover_tools()] == ["t"]


def test_http_client_is_connected_after_connect():
    post, _ = make_http_post()
    c = MCPClient(MCPServerConfig(name="r", transport="http", url="https://x"), http_post=post)
    c.connect()
    assert c.is_connected() is True


def test_http_connect_without_url_errors():
    c = MCPClient(MCPServerConfig(name="r", transport="http"))
    assert c.connect().get("status") == "failed"
