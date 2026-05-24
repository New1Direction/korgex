"""
Korgex MCP Client — Native Model Context Protocol integration.

Connects to any MCP server (stdio or HTTP) and exposes its tools
through Korgex's tool abstraction layer.

Architecture:
    [MCP Server Process] ←──stdio──→ [MCPClient]
                                         │
                                    JSON-RPC 2.0
                                         │
                                         ▼
                              [Tool Registry Discovery]
                                         │
                                         ▼
                              [Registered into abstraction layer]
                                         │
                                         ▼
                              [Available as Read/Write/Bash, etc.]

Protocol: JSON-RPC 2.0 over stdio
Lifecycle: initialize → tools/list → tools/call (loop) → shutdown
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════
# DATA TYPES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""
    name: str
    description: str
    input_schema: dict
    server_name: str  # Which server provides this tool


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # "stdio" or "http"
    url: Optional[str] = None  # For HTTP transport
    timeout: int = 60


# ═══════════════════════════════════════════════════════════════════════════
# JSON-RPC 2.0 MESSAGING
# ═══════════════════════════════════════════════════════════════════════════

def make_request(method: str, params: dict = None, req_id: str = None) -> str:
    """Build a JSON-RPC 2.0 request."""
    if req_id is None:
        req_id = str(uuid.uuid4())[:8]
    msg = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
    }
    if params:
        msg["params"] = params
    return json.dumps(msg) + "\n"


def make_notification(method: str, params: dict = None) -> str:
    """Build a JSON-RPC 2.0 notification (no id — no response expected)."""
    msg = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params:
        msg["params"] = params
    return json.dumps(msg) + "\n"


def parse_response(line: str) -> dict:
    """Parse a JSON-RPC 2.0 response line."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"error": {"message": f"Invalid JSON: {line[:200]}"}}


# ═══════════════════════════════════════════════════════════════════════════
# MCP CLIENT — stdio transport
# ═══════════════════════════════════════════════════════════════════════════

class MCPClient:
    """A single MCP server connection over stdio transport.
    
    Lifecycle:
        1. connect() — spawn process, send initialize handshake
        2. discover_tools() — tools/list → returns MCPTool[]
        3. call_tool(name, args) — tools/call → returns result
        4. disconnect() — send shutdown notification, kill process
    """
    
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._connected = False
        self._capabilities: dict = {}
        self._tools: list[MCPTool] = []
        self._pending_requests: dict[str, threading.Event] = {}
        self._pending_results: dict[str, dict] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_buffer: list[str] = []
    
    # ── Connection ───────────────────────────────────────────────────
    
    def connect(self) -> dict:
        """Spawn the server process and perform initialize handshake."""
        if self._connected:
            return {"status": "already_connected"}
        
        try:
            env = os.environ.copy()
            env.update(self.config.env)
            
            self._process = subprocess.Popen(
                [self.config.command] + self.config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,  # Line-buffered
            )
        except FileNotFoundError as e:
            return {"error": f"Command not found: {self.config.command}", "status": "failed"}
        except Exception as e:
            return {"error": f"Failed to spawn: {e}", "status": "failed"}
        
        # Start background reader for stderr
        self._reader_thread = threading.Thread(
            target=self._read_stderr, daemon=True
        )
        self._reader_thread.start()

        # Start stdout reader BEFORE the first _send_request so that
        # pending-request Events are dispatched as soon as the server responds.
        # (Must be running when we send "initialize", not after.)
        stdout_reader = threading.Thread(target=self.start_response_reader, daemon=True)
        stdout_reader.start()

        # Send initialize request
        result = self._send_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {
                "tools": {
                    "listChanged": True,
                    "supportsStreaming": False,
                }
            },
            "clientInfo": {
                "name": "korgex",
                "version": "1.0.0",
            },
        })
        
        if "error" in result:
            self._cleanup()
            return {"error": f"Handshake failed: {result['error']}", "status": "failed"}
        
        self._capabilities = result.get("result", {}).get("capabilities", {})
        
        # Send initialized notification
        self._send_notification("notifications/initialized", {})
        
        self._connected = True

        return {
            "status": "connected",
            "server": self.config.name,
            "capabilities": list(self._capabilities.keys()),
        }
    
    def disconnect(self):
        """Gracefully shut down the server connection."""
        if self._connected:
            try:
                self._send_notification("shutdown", {})
            except Exception:
                pass
            self._cleanup()
    
    def _cleanup(self):
        """Kill the subprocess and clean up."""
        self._connected = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
    
    # ── Tool Discovery ───────────────────────────────────────────────
    
    def discover_tools(self) -> list[MCPTool]:
        """Fetch the list of available tools from the server."""
        if not self._connected:
            return []
        
        result = self._send_request("tools/list", {})
        
        if "error" in result:
            return []
        
        tools_data = result.get("result", {}).get("tools", [])
        
        self._tools = [
            MCPTool(
                name=t.get("name", "unknown"),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.config.name,
            )
            for t in tools_data
        ]
        
        return self._tools
    
    # ── Tool Execution ───────────────────────────────────────────────
    
    def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server."""
        if not self._connected:
            return {"error": "Not connected to server"}
        
        result = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        
        if "error" in result:
            return {"error": str(result["error"])}
        
        return result.get("result", {})
    
    # ── Internal: JSON-RPC over stdio ─────────────────────────────────
    
    def _send_request(self, method: str, params: dict = None) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        req_id = f"req_{self._request_id}"
        self._request_id += 1
        
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params:
            request["params"] = params
        
        event = threading.Event()
        self._pending_requests[req_id] = event
        
        with self._lock:
            self._write_line(json.dumps(request))
        
        # Wait for response with timeout
        if not event.wait(timeout=self.config.timeout):
            self._pending_requests.pop(req_id, None)
            return {"error": f"Request timed out after {self.config.timeout}s"}
        
        result = self._pending_results.pop(req_id, {})
        return result
    
    def _send_notification(self, method: str, params: dict = None):
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            notification["params"] = params
        
        with self._lock:
            self._write_line(json.dumps(notification))
    
    def _write_line(self, line: str):
        """Write a line to the server's stdin."""
        if self._process and self._process.stdin:
            self._process.stdin.write(line + "\n")
            self._process.stdin.flush()
    
    def _read_stderr(self):
        """Background thread: read stderr for diagnostics."""
        if self._process and self._process.stderr:
            for line in self._process.stderr:
                self._stderr_buffer.append(line.rstrip())
    
    def start_response_reader(self):
        """Start reading stdout responses (run in a thread)."""
        if self._process and self._process.stdout:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # Check if this is a response to a pending request
                req_id = response.get("id")
                if req_id and req_id in self._pending_requests:
                    self._pending_results[req_id] = response
                    self._pending_requests[req_id].set()
    
    def is_connected(self) -> bool:
        return self._connected and self._process is not None and self._process.poll() is None
    
    def get_stderr_log(self) -> list[str]:
        return self._stderr_buffer.copy()
    
    def get_stats(self) -> dict:
        return {
            "server": self.config.name,
            "connected": self._connected,
            "alive": self.is_connected(),
            "tools_discovered": len(self._tools),
            "capabilities": list(self._capabilities.keys()),
            "stderr_lines": len(self._stderr_buffer),
        }


# ═══════════════════════════════════════════════════════════════════════════
# MCP SERVER MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class MCPServerManager:
    """Manages multiple MCP server connections.
    
    Handles:
    - Adding/removing server configs
    - Connecting to all configured servers
    - Aggregating all discovered tools across servers
    - Tool routing: finds the right server for a tool name
    """
    
    def __init__(self):
        self._servers: dict[str, MCPClient] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._tool_index: dict[str, str] = {}  # tool_name → server_name
    
    def add_server(self, config: MCPServerConfig) -> dict:
        """Add and connect to an MCP server."""
        if config.name in self._servers:
            return {"error": f"Server '{config.name}' already exists"}
        
        client = MCPClient(config)
        result = client.connect()
        
        if "error" in result:
            return result
        
        # Discover tools
        tools = client.discover_tools()
        
        self._servers[config.name] = client
        self._configs[config.name] = config
        
        # Index tools
        for tool in tools:
            self._tool_index[tool.name] = config.name
        
        return {
            "status": "connected",
            "server": config.name,
            "tools_found": len(tools),
            "tool_names": [t.name for t in tools],
        }
    
    def remove_server(self, name: str) -> dict:
        """Disconnect and remove an MCP server."""
        if name not in self._servers:
            return {"error": f"Server '{name}' not found"}
        
        self._servers[name].disconnect()
        
        # Remove from tool index
        tools_to_remove = [k for k, v in self._tool_index.items() if v == name]
        for t in tools_to_remove:
            del self._tool_index[t]
        
        del self._servers[name]
        del self._configs[name]
        
        return {"status": "removed", "server": name, "tools_removed": len(tools_to_remove)}
    
    def list_servers(self) -> list[dict]:
        """List all connected servers with stats."""
        return [
            client.get_stats()
            for name, client in self._servers.items()
        ]
    
    def get_all_tools(self) -> list[MCPTool]:
        """Get all tools from all connected servers."""
        tools = []
        for client in self._servers.values():
            tools.extend(client._tools)
        return tools
    
    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Route a tool call to the correct server."""
        server_name = self._tool_index.get(tool_name)
        if not server_name:
            return {"error": f"Tool '{tool_name}' not found on any server"}
        
        client = self._servers.get(server_name)
        if not client:
            return {"error": f"Server '{server_name}' for tool '{tool_name}' is not connected"}
        
        return client.call_tool(tool_name, arguments)
    
    def shutdown_all(self):
        """Disconnect all servers."""
        for client in self._servers.values():
            client.disconnect()
        self._servers.clear()
        self._configs.clear()
        self._tool_index.clear()


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG PARSING
# ═══════════════════════════════════════════════════════════════════════════

def load_mcp_config(path: str = None) -> dict[str, MCPServerConfig]:
    """Load MCP server configurations from a JSON file.
    
    Expected format (matches VS Code's mcp.json convention):
    {
        "mcpServers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "..."}
            },
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
            }
        }
    }
    """
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "..", "mcp.json")
    
    if not os.path.exists(path):
        return {}
    
    with open(path) as f:
        data = json.load(f)
    
    servers = data.get("mcpServers", {})
    configs = {}
    
    for name, cfg in servers.items():
        configs[name] = MCPServerConfig(
            name=name,
            command=cfg.get("command", ""),
            args=cfg.get("args", []),
            env=cfg.get("env", {}),
            timeout=cfg.get("timeout", 60),
        )
    
    return configs


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_manager = None

def get_manager() -> MCPServerManager:
    """Get or create the global MCP server manager."""
    global _manager
    if _manager is None:
        _manager = MCPServerManager()
    return _manager


# ═══════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== MCP Client Module Test ===\n")
    
    # Verify all components load
    print(f"  MCPClient class: ✓")
    print(f"  MCPServerManager class: ✓")
    print(f"  MCPTool dataclass: ✓")
    print(f"  MCPServerConfig dataclass: ✓")
    print(f"  make_request: ✓")
    print(f"  parse_response: ✓")
    print(f"  load_mcp_config: ✓")
    
    # Test JSON-RPC message format
    req = make_request("initialize", {"protocolVersion": "2025-03-26"})
    parsed = json.loads(req)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["method"] == "initialize"
    print(f"\n  ✓ JSON-RPC 2.0 message format verified")
    
    # Test config loading
    test_config = """{
        "mcpServers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "test"}
            }
        }
    }"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(test_config)
        tmp_path = f.name
    
    configs = load_mcp_config(tmp_path)
    assert "github" in configs
    assert configs["github"].command == "npx"
    assert configs["github"].args == ["-y", "@modelcontextprotocol/server-github"]
    os.unlink(tmp_path)
    print(f"  ✓ MCP config parsing verified")
    print(f"\n  Ready: {len(configs)} servers from config")