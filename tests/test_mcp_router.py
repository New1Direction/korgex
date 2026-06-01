"""Tests for the MCP router/compose layer (src/mcp_router.py).

The router aggregates N MCP servers behind one façade. Its whole reason to exist
is to fix the flat-index shadowing bug in the legacy MCPServerManager: when two
servers expose a tool with the same name, BOTH must remain reachable. We prove
that here, plus namespacing, routing, graceful degradation, and reverse-handler
propagation — all with an injected fake client so no subprocess is spawned.
"""
from src.mcp_client import MCPServerConfig, MCPTool
from src.mcp_router import MCPRouter, namespaced_name, parse_namespaced


# ── A fake MCPClient: same surface, no subprocess ────────────────────────────

class FakeClient:
    def __init__(self, config, tools, fail=False):
        self.config = config
        self._tools = tools
        self._fail = fail
        self.connected = False
        self.calls = []          # [(tool_name, args), ...]
        self.reverse = None      # (asker, sampler)
        self.disconnected = False

    def connect(self):
        if self._fail:
            return {"error": "spawn failed", "status": "failed"}
        self.connected = True
        return {"status": "connected", "server": self.config.name}

    def discover_tools(self):
        return [MCPTool(name=n, description="d", input_schema={}, server_name=self.config.name)
                for n in self._tools]

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"ok": True, "server": self.config.name, "tool": name}

    def set_reverse_handlers(self, *, asker=None, sampler=None):
        self.reverse = (asker, sampler)

    def disconnect(self):
        self.connected = False
        self.disconnected = True

    def get_stats(self):
        return {"server": self.config.name, "connected": self.connected,
                "tools_discovered": len(self._tools)}


def make_factory(tools_by_server, fail=()):
    """Build a client_factory + a dict that captures the clients it creates."""
    created = {}

    def factory(config):
        c = FakeClient(config, tools_by_server.get(config.name, []), config.name in fail)
        created[config.name] = c
        return c
    return factory, created


def cfg(name):
    return MCPServerConfig(name=name, command="noop")


# ── Pure namespacing helpers ─────────────────────────────────────────────────

def test_namespaced_name_joins_server_and_tool():
    assert namespaced_name("github", "create_issue") == "github__create_issue"


def test_parse_namespaced_splits_on_first_separator():
    # Tool names may themselves contain "__"; the server is the prefix up to the
    # FIRST separator, so the original tool name is recovered intact.
    assert parse_namespaced("github__create_issue") == ("github", "create_issue")
    assert parse_namespaced("fs__read__file") == ("fs", "read__file")


def test_parse_namespaced_without_separator_is_unrouted():
    assert parse_namespaced("bare_tool") == (None, "bare_tool")


def test_namespaced_name_is_model_safe():
    # Model-facing tool names must match [a-zA-Z0-9_-]+ — no ".", "/", or "::".
    import re
    n = namespaced_name("my-server", "do_thing")
    assert re.fullmatch(r"[A-Za-z0-9_-]+", n)


# ── Compose / connect ────────────────────────────────────────────────────────

def test_connect_all_reports_connected_servers():
    factory, _ = make_factory({"alpha": ["a"], "beta": ["b"]})
    r = MCPRouter(client_factory=factory)
    report = r.connect_all({"alpha": cfg("alpha"), "beta": cfg("beta")})
    assert set(report["connected"]) == {"alpha", "beta"}
    assert report["failed"] == {}
    assert report["tools"] == 2


def test_one_server_failing_does_not_kill_the_rest():
    factory, created = make_factory({"alpha": ["a"], "beta": ["b"]}, fail={"beta"})
    r = MCPRouter(client_factory=factory)
    report = r.connect_all({"alpha": cfg("alpha"), "beta": cfg("beta")})
    assert report["connected"] == ["alpha"]
    assert "beta" in report["failed"]
    # alpha's tool is present; beta contributed nothing.
    names = [t.name for t in r.discover_tools()]
    assert names == ["alpha__a"]


# ── The headline fix: no silent shadowing ────────────────────────────────────

def test_two_servers_same_tool_both_reachable():
    factory, _ = make_factory({"alpha": ["read_file"], "beta": ["read_file"]})
    r = MCPRouter(client_factory=factory)
    r.connect_all({"alpha": cfg("alpha"), "beta": cfg("beta")})
    names = sorted(t.name for t in r.discover_tools())
    # The legacy flat manager would keep only ONE "read_file"; the router keeps both.
    assert names == ["alpha__read_file", "beta__read_file"]


# ── Routing ──────────────────────────────────────────────────────────────────

def test_call_tool_routes_to_owning_server_with_original_name():
    factory, created = make_factory({"alpha": ["read_file"], "beta": ["read_file"]})
    r = MCPRouter(client_factory=factory)
    r.connect_all({"alpha": cfg("alpha"), "beta": cfg("beta")})

    out = r.call_tool("beta__read_file", {"path": "/x"})

    # Routed to beta only, and beta received the ORIGINAL (un-namespaced) name.
    assert out["server"] == "beta"
    assert created["beta"].calls == [("read_file", {"path": "/x"})]
    assert created["alpha"].calls == []


def test_call_unknown_tool_returns_error_not_exception():
    factory, _ = make_factory({"alpha": ["a"]})
    r = MCPRouter(client_factory=factory)
    r.connect_all({"alpha": cfg("alpha")})
    out = r.call_tool("ghost__missing", {})
    assert "error" in out


# ── Reverse handlers (elicitation/sampling) propagate to every client ─────────

def test_set_reverse_handlers_propagates_to_all_clients():
    factory, created = make_factory({"alpha": ["a"], "beta": ["b"]})
    r = MCPRouter(client_factory=factory)
    r.connect_all({"alpha": cfg("alpha"), "beta": cfg("beta")})

    asker, sampler = object(), object()
    r.set_reverse_handlers(asker=asker, sampler=sampler)

    assert created["alpha"].reverse == (asker, sampler)
    assert created["beta"].reverse == (asker, sampler)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def test_shutdown_all_disconnects_every_client():
    factory, created = make_factory({"alpha": ["a"], "beta": ["b"]})
    r = MCPRouter(client_factory=factory)
    r.connect_all({"alpha": cfg("alpha"), "beta": cfg("beta")})
    r.shutdown_all()
    assert created["alpha"].disconnected
    assert created["beta"].disconnected
    assert r.discover_tools() == []


def test_connect_all_is_idempotent_for_connected_servers():
    # The REPL rebuilds the agent on /clear and /model, re-entering server boot.
    # A second connect_all must NOT re-spawn a live server or double its tools.
    factory, created = make_factory({"alpha": ["a"]})
    r = MCPRouter(client_factory=factory)
    r.connect_all({"alpha": cfg("alpha")})
    first = created["alpha"]

    report = r.connect_all({"alpha": cfg("alpha")})

    assert report["connected"] == ["alpha"]
    assert [t.name for t in r.discover_tools()] == ["alpha__a"]   # not doubled
    assert created["alpha"] is first                              # same client, no re-spawn


def test_has_tool_reports_membership():
    factory, _ = make_factory({"alpha": ["read_file"]})
    r = MCPRouter(client_factory=factory)
    r.connect_all({"alpha": cfg("alpha")})
    assert r.has_tool("alpha__read_file")
    assert not r.has_tool("alpha__nope")


# ── Integration: route_tool_call dispatches namespaced tools via the router ──

def test_route_tool_call_dispatches_namespaced_tool_via_router(monkeypatch):
    import src.mcp_router as mcp_router
    from src.tool_abstraction import (register_mcp_tool, route_tool_call,
                                       unregister_mcp_tools)

    factory, created = make_factory({"alpha": ["read_file"]})
    r = MCPRouter(client_factory=factory)
    r.connect_all({"alpha": cfg("alpha")})
    monkeypatch.setattr(mcp_router, "get_router", lambda: r)

    register_mcp_tool(r.discover_tools()[0])  # registers "alpha__read_file"
    try:
        out = route_tool_call("alpha__read_file", {"path": "/x"})
        assert out["server"] == "alpha"
        # The owning client got the ORIGINAL (un-namespaced) tool name.
        assert created["alpha"].calls == [("read_file", {"path": "/x"})]
    finally:
        unregister_mcp_tools()
