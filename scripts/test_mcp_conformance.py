"""
MCP Conformance Test: Connect KorgKode's MCP client to a real server,
discover its tools, and execute a tool call.

This is the proof that KorgKode's MCP architecture works outside 
Anthropic's walled garden — connecting to a standard, open-source MCP server.
"""

import json
import os
import subprocess
import sys
import time

# Add KorgKode to path
sys.path.insert(0, os.path.expanduser("~/KorgKode"))

from src.mcp_client import MCPServerConfig, get_manager, MCPTool, make_request


def test_filesystem_mcp():
    """Connect to the official filesystem MCP server and execute a query."""
    
    manager = get_manager()
    
    # 1. Connect to the filesystem MCP server
    print("=" * 60)
    print("MCP CONFORMANCE TEST")
    print("=" * 60)
    print()
    print(f"Server: @modelcontextprotocol/server-filesystem")
    print(f"Transport: stdio (subprocess)")
    print()
    
    config = MCPServerConfig(
        name="fs-test",
        command="npx",
        args=[
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/tmp",
        ],
        timeout=15,
    )
    
    print(f"[1/5] Spawning server...", end=" ")
    sys.stdout.flush()
    result = manager.add_server(config)
    
    if "error" in result:
        print(f"FAILED: {result['error']}")
        return False
    print(f"OK — status={result['status']}")
    
    # 2. List all connected servers
    print(f"[2/5] Listing servers...", end=" ")
    servers = manager.list_servers()
    print(f"OK — {len(servers)} server(s)")
    for s in servers:
        print(f"       {s['server']}: {s['tools_discovered']} tools, alive={s['alive']}")
    
    # 3. List discovered tools
    print(f"[3/5] Listing tools...", end=" ")
    tools = manager.get_all_tools()
    print(f"OK — {len(tools)} tool(s) discovered")
    for t in tools:
        props = list(t.input_schema.get("properties", {}).keys())
        print(f"       {t.name}: {t.description[:60]}... params={props}")
    
    # 4. Execute a tool call — read a file
    print(f"[4/5] Executing tool call...", end=" ")
    sys.stdout.flush()
    
    # Write a test file
    with open("/tmp/korgkode_mcp_test.txt", "w") as f:
        f.write("KorgKode MCP integration verified: " + time.ctime())
    
    result = manager.call_tool("read_file", {
        "path": "/tmp/korgkode_mcp_test.txt"
    })
    
    if "error" in result:
        print(f"FAILED: {result['error']}")
        return False
    
    content = result.get("content", [])
    text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
    print(f"OK")
    print(f"       Result: {text[:80]}")
    
    # 5. Execute a search tool call
    print(f"[5/5] Executing search...", end=" ")
    sys.stdout.flush()
    
    result = manager.call_tool("search", {
        "path": "/tmp",
        "pattern": "korgkode_mcp_test*"
    })
    
    if "error" in result:
        print(f"FAILED: {result['error']}")
        return False
    
    content = result.get("content", [])
    search_text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
    print(f"OK")
    print(f"       Found: {search_text[:80]}")
    
    # Cleanup
    os.unlink("/tmp/korgkode_mcp_test.txt")
    
    print()
    print("=" * 60)
    print("MCP CONFORMANCE: PASSED")
    print("=" * 60)
    print()
    print("KorgKode successfully:")
    print("  - Spawned a standard MCP server as a subprocess")
    print("  - Performed the JSON-RPC 2.0 initialize handshake")
    print("  - Discovered all server tools via tools/list")
    print("  - Executed a tool call via tools/call (read_file)")
    print("  - Executed a second tool call (search)")
    print()
    print("This is an open, standard protocol — not tied to any vendor.")
    
    # Disconnect
    manager.remove_server("fs-test")
    
    return True


if __name__ == "__main__":
    success = test_filesystem_mcp()
    sys.exit(0 if success else 1)