"""ACP client-capability completion: session/load (resume) + forwarding the client's
mcpServers. session/load re-registers a session and the bridge resumes it from the
repo's ledger; forwarded mcpServers are translated and connected into the agent.
"""
from src import acp
from src.plugins import PluginRegistry


def _req(mid, method, params=None):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}


# ── capability + translator ─────────────────────────────────────────────────────

def test_initialize_advertises_load_session():
    r = acp.AcpAgent().handle(_req(1, "initialize", {"protocolVersion": 1}))["result"]
    assert r["agentCapabilities"]["loadSession"] is True


def test_mcp_servers_to_config_stdio_and_http():
    out = acp.mcp_servers_to_config([
        {"name": "git", "command": "uvx", "args": ["mcp-server-git"], "env": {"X": "1"}},
        {"name": "linear", "url": "https://mcp.linear.app/mcp", "headers": {"Authorization": "Bearer t"}},
        {"command": "noname"},   # missing name → dropped
    ])
    assert out["git"] == {"command": "uvx", "args": ["mcp-server-git"], "env": {"X": "1"}}
    assert out["linear"] == {"url": "https://mcp.linear.app/mcp",
                             "headers": {"Authorization": "Bearer t"}}
    assert len(out) == 2


# ── session/load ────────────────────────────────────────────────────────────────

def test_session_load_registers_a_resumable_session():
    a = acp.AcpAgent()
    res = a.handle(_req(2, "session/load",
                        {"sessionId": "prev-123", "cwd": "/repo", "mcpServers": []}))
    assert res["result"] == {}
    sess = a.sessions["prev-123"]
    assert sess["resumed"] is True and sess["cwd"] == "/repo"


def test_session_load_requires_a_session_id():
    resp = acp.AcpAgent().handle(_req(3, "session/load", {"cwd": "/r"}))
    assert "error" in resp and resp["error"]["code"] == -32602


# ── bridge: forward mcpServers + resume a loaded session ────────────────────────

class _StubAgent:
    def __init__(self):
        self.plugins = PluginRegistry()
        self.repo_root = None
        self.connected = None
        self.last_resume = "UNSET"

    def connect_mcp_configs(self, configs):
        self.connected = configs
        return 0

    def run_task(self, prompt, resume_context=None):
        self.last_resume = resume_context
        return {"success": True, "result": "ok"}


def test_bridge_forwards_client_mcp_servers():
    holder = {}
    rt = acp.make_live_run_turn(lambda: holder.setdefault("a", _StubAgent()))
    a = acp.AcpAgent(run_turn=rt, send=lambda m: None)
    sid = a.handle(_req(1, "session/new",
                        {"mcpServers": [{"name": "git", "command": "uvx", "args": ["mcp-server-git"]}]}))["result"]["sessionId"]
    a.handle(_req(2, "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "go"}]}))
    assert holder["a"].connected == {"git": {"command": "uvx", "args": ["mcp-server-git"]}}


def test_bridge_passes_resume_context_for_a_loaded_session():
    holder = {}
    rt = acp.make_live_run_turn(lambda: holder.setdefault("a", _StubAgent()),
                                resume_builder=lambda cwd: "PRIOR TRANSCRIPT")
    a = acp.AcpAgent(run_turn=rt, send=lambda m: None)
    a.handle(_req(1, "session/load", {"sessionId": "s1", "cwd": "/repo"}))
    a.handle(_req(2, "session/prompt", {"sessionId": "s1", "prompt": [{"type": "text", "text": "continue"}]}))
    assert holder["a"].last_resume == "PRIOR TRANSCRIPT"


def test_bridge_no_resume_for_a_fresh_session():
    holder = {}
    rt = acp.make_live_run_turn(lambda: holder.setdefault("a", _StubAgent()),
                                resume_builder=lambda cwd: "SHOULD NOT BE USED")
    a = acp.AcpAgent(run_turn=rt, send=lambda m: None)
    sid = a.handle(_req(1, "session/new", {"cwd": "/repo"}))["result"]["sessionId"]
    a.handle(_req(2, "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "go"}]}))
    assert holder["a"].last_resume is None      # fresh session → no resume context
