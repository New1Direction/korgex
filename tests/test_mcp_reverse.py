"""MCP reverse-requests: elicitation + sampling (client-side handlers).

Most MCP clients only call servers. These let a server call BACK:
  - elicitation/create   — the server asks the USER a question mid-tool-call.
  - sampling/createMessage — the server borrows the CLIENT's LLM for a completion.
korgex advertises both capabilities and answers them. The handlers are pure
(asker / sampler injected), so they're testable with no stdin or network.
"""
from src import mcp_reverse as MR


# ── capability advertisement ───────────────────────────────────────────────────

def test_client_capabilities_include_elicitation_and_sampling():
    caps = MR.client_capabilities()
    assert "elicitation" in caps and "sampling" in caps


# ── elicitation: server asks the user a question ───────────────────────────────

def test_elicitation_returns_user_answer():
    req = {"id": "r1", "method": "elicitation/create",
           "params": {"message": "Which environment?", "requestedSchema": {"type": "string"}}}
    resp = MR.handle_reverse(req, asker=lambda prompt, schema: "production", sampler=None)
    assert resp["id"] == "r1"
    assert resp["result"]["action"] == "accept"
    assert resp["result"]["content"] == "production"


def test_elicitation_decline_when_user_gives_nothing():
    req = {"id": "r2", "method": "elicitation/create", "params": {"message": "PIN?"}}
    resp = MR.handle_reverse(req, asker=lambda p, s: "", sampler=None)
    assert resp["result"]["action"] == "decline"  # empty → declined, not a fake value


def test_elicitation_failsafe_declines_when_asker_errors():
    req = {"id": "r3", "method": "elicitation/create", "params": {"message": "?"}}
    def boom(p, s): raise RuntimeError("no tty")
    resp = MR.handle_reverse(req, asker=boom, sampler=None)
    assert resp["result"]["action"] == "decline"  # never crash the server's call


# ── sampling: server borrows the client's LLM ──────────────────────────────────

def test_sampling_returns_model_completion():
    req = {"id": "s1", "method": "sampling/createMessage",
           "params": {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}],
                      "maxTokens": 100}}
    resp = MR.handle_reverse(req, asker=None,
                             sampler=lambda msgs, sys, max_tokens: "hello back")
    assert resp["id"] == "s1"
    assert resp["result"]["role"] == "assistant"
    assert resp["result"]["content"]["text"] == "hello back"
    assert "model" in resp["result"]


def test_sampling_failsafe_errors_cleanly():
    req = {"id": "s2", "method": "sampling/createMessage", "params": {"messages": []}}
    def boom(*a, **k): raise RuntimeError("model down")
    resp = MR.handle_reverse(req, asker=None, sampler=boom)
    assert "error" in resp and resp["id"] == "s2"  # JSON-RPC error, not a crash


# ── unknown reverse method → JSON-RPC method-not-found ─────────────────────────

def test_unknown_reverse_method_is_method_not_found():
    req = {"id": "x", "method": "roots/list", "params": {}}
    resp = MR.handle_reverse(req, asker=lambda p, s: "x", sampler=lambda *a: "y")
    assert resp["error"]["code"] == -32601  # method not found


def test_is_reverse_request_detects_server_calls():
    # a server→client REQUEST has an id + a known reverse method
    assert MR.is_reverse_request({"id": "1", "method": "elicitation/create"})
    assert MR.is_reverse_request({"id": "2", "method": "sampling/createMessage"})
    # a plain response (no method) or our own request isn't a reverse request
    assert not MR.is_reverse_request({"id": "3", "result": {}})
    assert not MR.is_reverse_request({"method": "tools/list"})  # notification, no id


# ── integration: the MCP client advertises + binds handlers ────────────────────

def test_client_advertises_reverse_capabilities():
    from src.mcp_client import MCPClient, MCPServerConfig
    c = MCPClient(MCPServerConfig(name="t", command=["true"]))
    # the capabilities sent in the handshake include elicitation + sampling
    from src import mcp_reverse
    caps = mcp_reverse.client_capabilities()
    assert "elicitation" in caps and "sampling" in caps


def test_client_set_reverse_handlers_binds():
    from src.mcp_client import MCPClient, MCPServerConfig
    c = MCPClient(MCPServerConfig(name="t", command=["true"]))
    assert c._reverse_asker is None and c._reverse_sampler is None
    c.set_reverse_handlers(asker=lambda m, s: "yes", sampler=lambda m, sy, mx: "out")
    assert c._reverse_asker("?", None) == "yes"
    assert c._reverse_sampler([], None, 10) == "out"
    # a reverse request now routes through the bound handler
    from src import mcp_reverse
    resp = mcp_reverse.handle_reverse(
        {"id": "1", "method": "elicitation/create", "params": {"message": "ok?"}},
        asker=c._reverse_asker, sampler=c._reverse_sampler)
    assert resp["result"]["content"] == "yes"
