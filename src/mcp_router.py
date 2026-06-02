"""Korgex MCP Router — compose many MCP servers behind one namespaced façade.

The legacy ``MCPServerManager`` indexes tools by bare name (``tool_name -> server``),
so two servers exposing the same tool name silently shadow each other — only the
last one wins. The router fixes that by **namespacing** every tool as
``server__tool`` and keeping a reverse routing map, so every server's tools stay
reachable and each call routes to the owning server (with the server's *original*
tool name).

It also turns "connect all configured servers" into one compose step that
**degrades gracefully**: a server failing to spawn is recorded and skipped while
the rest stay up, instead of aborting the batch.

Model-facing tool names must match ``[A-Za-z0-9_-]{1,64}``, so the namespace
separator is ``__`` — ``.``, ``/`` and ``::`` are all invalid inside a tool name.
"""
from __future__ import annotations

from src.mcp_client import MCPClient, MCPTool

SEP = "__"


def namespaced_name(server: str, tool: str) -> str:
    """Join a server + tool into one model-safe, collision-free tool name."""
    return f"{server}{SEP}{tool}"


def parse_namespaced(name: str):
    """Split a namespaced name back into ``(server, tool)``.

    Splits on the FIRST separator so a tool name that itself contains ``__``
    survives intact. A name with no separator is unrouted: ``(None, name)``.
    """
    if SEP in name:
        server, _, tool = name.partition(SEP)
        return server, tool
    return None, name


class MCPRouter:
    """Aggregates multiple MCPClient connections behind one namespaced surface.

    `client_factory(config)` builds a per-server client (defaults to the real
    ``MCPClient``; tests inject a fake so no subprocess is spawned). Every client
    just needs: ``connect()``, ``discover_tools()``, ``call_tool(name, args)``,
    ``disconnect()``, ``set_reverse_handlers(...)``, ``get_stats()``.
    """

    def __init__(self, client_factory=MCPClient):
        self._client_factory = client_factory
        self._clients: dict = {}                 # server name -> client
        self._tools: list = []                   # namespaced MCPTool list
        self._route: dict = {}                   # namespaced name -> (server, original)
        self._reverse = (None, None)             # (asker, sampler) to apply to clients

    def connect_all(self, configs: dict) -> dict:
        """Connect every configured server. Servers connect (and discover tools) in
        PARALLEL — startup with several servers is bounded by the slowest one, not
        their sum. Degrades gracefully: a server that fails is recorded in ``failed``
        and skipped. Results are assembled in config order, so output is deterministic."""
        from concurrent.futures import ThreadPoolExecutor

        connected, failed = [], {}
        pending = [(n, c) for n, c in configs.items() if n not in self._clients]

        def _connect_one(name, config):
            client = self._client_factory(config)
            res = client.connect()
            if isinstance(res, dict) and res.get("error"):
                return client, res["error"], []
            asker, sampler = self._reverse
            if asker is not None or sampler is not None:
                try:
                    client.set_reverse_handlers(asker=asker, sampler=sampler)
                except Exception:
                    pass
            return client, None, list(client.discover_tools())   # discover in the worker too

        results = {}
        if pending:
            with ThreadPoolExecutor(max_workers=min(8, len(pending))) as ex:
                futs = {ex.submit(_connect_one, n, c): n for n, c in pending}
                for fut in futs:
                    name = futs[fut]
                    try:
                        results[name] = fut.result()
                    except Exception as e:
                        results[name] = (None, f"{type(e).__name__}: {e}", [])

        # assemble in CONFIG order (deterministic) — no network here, just registration
        for name in configs:
            if name in self._clients:
                connected.append(name)               # already live (e.g. /clear rebuild)
                continue
            if name not in results:
                continue
            client, err, tools = results[name]
            if err or client is None:
                failed[name] = err or "connect failed"
                continue
            self._clients[name] = client
            connected.append(name)
            for t in tools:
                ns = namespaced_name(name, t.name)
                self._tools.append(MCPTool(name=ns, description=t.description,
                                           input_schema=t.input_schema, server_name=name))
                self._route[ns] = (name, t.name)
        return {"connected": connected, "failed": failed, "tools": len(self._tools)}

    def discover_tools(self) -> list:
        """All tools across all connected servers, namespaced."""
        return list(self._tools)

    def has_tool(self, name: str) -> bool:
        """True if `name` is a namespaced tool this router can route."""
        return name in self._route

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Route a namespaced tool call to the owning server, using that server's
        ORIGINAL (un-namespaced) tool name."""
        route = self._route.get(name)
        if route is None:
            return {"error": f"Tool '{name}' not found on any connected server"}
        server, original = route
        client = self._clients.get(server)
        if client is None:
            return {"error": f"Server '{server}' for tool '{name}' is not connected"}
        return client.call_tool(original, arguments)

    def set_reverse_handlers(self, *, asker=None, sampler=None) -> None:
        """Bind elicitation/sampling handlers: propagate to all current clients and
        remember them for servers connected later."""
        self._reverse = (asker, sampler)
        for client in self._clients.values():
            try:
                client.set_reverse_handlers(asker=asker, sampler=sampler)
            except Exception:
                pass

    def list_servers(self) -> list:
        return [c.get_stats() for c in self._clients.values()]

    def shutdown_all(self) -> None:
        for client in self._clients.values():
            try:
                client.disconnect()
            except Exception:
                pass
        self._clients.clear()
        self._tools.clear()
        self._route.clear()


# ── Singleton ─────────────────────────────────────────────────────────────────
# The agent boots servers into this shared router at startup; route_tool_call
# consults it (falling back to the legacy manager for any bare-name tools).

_router = None


def get_router() -> MCPRouter:
    """Get or create the process-wide MCP router."""
    global _router
    if _router is None:
        _router = MCPRouter()
    return _router
