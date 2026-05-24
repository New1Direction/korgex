"""mcp-native-client — A production-grade, headless MCP client for Python.

Connects to any MCP (Model Context Protocol) server over stdio transport,
discovers its tools via JSON-RPC 2.0, and executes tool calls.

Not locked to Claude Desktop or any vendor. Pure Python, no GUI, no UI.
"""

from .client import MCPClient, MCPServerManager, MCPServerConfig, MCPTool

__all__ = ["MCPClient", "MCPServerManager", "MCPServerConfig", "MCPTool"]
__version__ = "0.1.0"
