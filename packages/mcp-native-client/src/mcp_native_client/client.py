"""
mcp-native-client — MCP client core.

Connects to any MCP server over stdio transport, discovers tools via
JSON-RPC 2.0, and executes tool calls.
"""

import json
import os
import subprocess
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
    server_name: str


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = 60


# ═══════════════════════════════════════════════════════════════════════════
# MCP CLIENT
# ═══════════════════════════════════════════════════════════════════════════

class MCPClient:
    """A single MCP server connection over stdio transport."""

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
        self._response_thread: Optional[threading.Thread] = None
        self._stderr_buffer: list[str] = []

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
                env=env, text=True, bufsize=1,
            )
        except FileNotFoundError as e:
            return {"error": f"Command not found: {self.config.command}", "status": "failed"}
        except Exception as e:
            return {"error": f"Failed to spawn: {e}", "status": "failed"}

        self._reader_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader_thread.start()
        self._response_thread = threading.Thread(
            target=self._read_stdout, daemon=True
        )
        self._response_thread.start()
        time.sleep(0.3)

        result = self._send_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": True}},
            "clientInfo": {"name": "mcp-native-client", "version": "0.1.0"},
        })

        if "error" in result:
            self._cleanup()
            return {"error": f"Handshake failed: {result['error']}", "status": "failed"}

        self._capabilities = result.get("result", {}).get("capabilities", {})
        self._send_notification("notifications/initialized", {})
        self._connected = True

        return {"status": "connected", "server": self.config.name,
                "capabilities": list(self._capabilities.keys())}

    def disconnect(self):
        if self._connected:
            try:
                self._send_notification("shutdown", {})
            except Exception:
                pass
            self._cleanup()

    def _cleanup(self):
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

    def discover_tools(self) -> list[MCPTool]:
        if not self._connected:
            return []
        result = self._send_request("tools/list", {})
        if "error" in result:
            return []
        tools_data = result.get("result", {}).get("tools", [])
        self._tools = [
            MCPTool(name=t.get("name", "unknown"),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                    server_name=self.config.name)
            for t in tools_data
        ]
        return self._tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        if not self._connected:
            return {"error": "Not connected to server"}
        result = self._send_request("tools/call", {"name": name, "arguments": arguments})
        if "error" in result:
            return {"error": str(result["error"])}
        return result.get("result", {})

    def _send_request(self, method: str, params: dict = None) -> dict:
        req_id = f"req_{self._request_id}"
        self._request_id += 1
        request = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params:
            request["params"] = params
        event = threading.Event()
        self._pending_requests[req_id] = event
        with self._lock:
            self._write_line(json.dumps(request))
        if not event.wait(timeout=self.config.timeout):
            self._pending_requests.pop(req_id, None)
            return {"error": f"Request timed out after {self.config.timeout}s"}
        return self._pending_results.pop(req_id, {})

    def _send_notification(self, method: str, params: dict = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        with self._lock:
            self._write_line(json.dumps(msg))

    def _write_line(self, line: str):
        if self._process and self._process.stdin:
            self._process.stdin.write(line + "\n")
            self._process.stdin.flush()

    def _read_stderr(self):
        if self._process and self._process.stderr:
            for line in self._process.stderr:
                self._stderr_buffer.append(line.rstrip())

    def _read_stdout(self):
        if self._process and self._process.stdout:
            for line in self._process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                req_id = response.get("id")
                if req_id and req_id in self._pending_requests:
                    self._pending_results[req_id] = response
                    self._pending_requests[req_id].set()

    def is_connected(self) -> bool:
        return self._connected and self._process is not None and self._process.poll() is None

    def get_stats(self) -> dict:
        return {
            "server": self.config.name,
            "connected": self._connected,
            "alive": self.is_connected(),
            "tools_discovered": len(self._tools),
        }


# ═══════════════════════════════════════════════════════════════════════════
# SERVER MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class MCPServerManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self._servers: dict[str, MCPClient] = {}
        self._tool_index: dict[str, str] = {}

    def add_server(self, config: MCPServerConfig) -> dict:
        if config.name in self._servers:
            return {"error": f"Server '{config.name}' already exists"}
        client = MCPClient(config)
        result = client.connect()
        if "error" in result:
            return result
        tools = client.discover_tools()
        self._servers[config.name] = client
        for tool in tools:
            self._tool_index[tool.name] = config.name
        return {"status": "connected", "server": config.name,
                "tools_found": len(tools),
                "tool_names": [t.name for t in tools]}

    def remove_server(self, name: str) -> dict:
        if name not in self._servers:
            return {"error": f"Server '{name}' not found"}
        self._servers[name].disconnect()
        tools_to_remove = [k for k, v in self._tool_index.items() if v == name]
        for t in tools_to_remove:
            del self._tool_index[t]
        del self._servers[name]
        return {"status": "removed", "server": name, "tools_removed": len(tools_to_remove)}

    def list_servers(self) -> list[dict]:
        return [c.get_stats() for c in self._servers.values()]

    def get_all_tools(self) -> list[MCPTool]:
        tools = []
        for client in self._servers.values():
            tools.extend(client._tools)
        return tools

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        server_name = self._tool_index.get(tool_name)
        if not server_name:
            return {"error": f"Tool '{tool_name}' not found on any server"}
        client = self._servers.get(server_name)
        if not client:
            return {"error": f"Server disconnected"}
        return client.call_tool(tool_name, arguments)

    def shutdown_all(self):
        for client in self._servers.values():
            client.disconnect()
        self._servers.clear()
        self._tool_index.clear()
