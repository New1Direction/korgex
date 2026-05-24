# mcp-native-client

**A production-grade, headless MCP client for Python.**

Not locked to Claude Desktop. Not locked to any vendor. Pure Python, stdio transport, JSON-RPC 2.0.

```python
from mcp_native_client import MCPServerManager, MCPServerConfig

manager = MCPServerManager()

# Connect to any MCP server
manager.add_server(MCPServerConfig(
    name="filesystem",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "."]
))

# All tools are auto-discovered
tools = manager.get_all_tools()
# → [read_file, write_file, edit_file, search_files, list_directory, ...]

# Call any tool
result = manager.call_tool("list_directory", {"path": "."})
```

## Why this exists

The MCP ecosystem has hundreds of servers and zero portable clients. Every agent vendor builds their own client and locks it to their platform. This library is:

- **Headless** — no GUI, no desktop app, no UI framework
- **Vendor-independent** — works with Anthropic, OpenAI, OpenRouter, or local models
- **Async-ready** — threading-based stdout reader, non-blocking by default
- **JSON-RPC 2.0** — implements the full MCP specification

## Install

```bash
pip install mcp-native-client
```

## Quick start

```python
from mcp_native_client import MCPServerManager, MCPServerConfig

manager = MCPServerManager()

# Connect to a server
result = manager.add_server(MCPServerConfig(
    name="github",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_TOKEN": "ghp_..."},
))

# List all tools across all servers
for tool in manager.get_all_tools():
    print(f"{tool.name}: {tool.description[:60]}")

# Call a tool
result = manager.call_tool("search_files", {
    "path": "/tmp",
    "pattern": "*.py",
})

# Disconnect
manager.remove_server("github")
```

## API

### `MCPServerConfig(name, command, args=[], env={}, timeout=60)`
Configuration for an MCP server connection.

### `MCPServerManager()`
Manages multiple server connections.

- `add_server(config)` — spawn + handshake + discover tools
- `remove_server(name)` — disconnect + remove tools
- `list_servers()` — stats for all servers
- `get_all_tools()` — all tools from all servers
- `call_tool(name, arguments)` — route to the right server
- `shutdown_all()` — disconnect everything

### `MCPTool(name, description, input_schema, server_name)`
A tool discovered from an MCP server.

## Protocol support

- Transport: stdio (subprocess)
- Protocol: MCP 2025-03-26
- Lifecycle: initialize → tools/list → tools/call → shutdown

## License

MIT