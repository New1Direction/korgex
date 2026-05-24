#!/usr/bin/env python3
"""Minimal MCP stub server for integration testing.

Speaks JSON-RPC 2.0 over stdio. Exposes one tool ("echo") that echoes
its arguments back as JSON text. Exits cleanly on a shutdown request.

When this file grows beyond ~100 lines of test scenarios, prefer
parameterising it (e.g. via env vars or CLI flags) over creating N
parallel stubs. Examples of scenarios worth adding:
  STUB_SLOW_INIT=5     — sleep before initialize response (timeout tests)
  STUB_BAD_TOOLS_JSON  — return malformed tools/list (error-handling tests)
  STUB_CRASH_ON_CALL   — exit mid-stream on tools/call (reconnect tests)
"""
import json
import sys


def write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except json.JSONDecodeError:
        continue

    req_id = req.get("id")
    method = req.get("method", "")

    if method == "initialize":
        write({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stub", "version": "0.0.1"},
        }})

    elif method == "tools/list":
        write({"jsonrpc": "2.0", "id": req_id, "result": {
            "tools": [{
                "name": "echo",
                "description": "Echo back arguments as JSON",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }],
        }})

    elif method == "tools/call":
        args = req.get("params", {}).get("arguments", {})
        write({"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps(args)}],
        }})

    elif method == "shutdown":
        sys.exit(0)

    # Notifications (no id) → no response needed
