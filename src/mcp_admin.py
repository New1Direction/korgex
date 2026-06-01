"""CRUD for MCP server config — backs `korgex mcp add/list/remove`.

Writes go to the native project config (mcp.json by default) so a user never has
to hand-edit JSON to wire up a server — stdio (command/args) or remote (url +
auth headers). `mcp_list` reads the full MERGED view (native + vendor-compat +
global) via mcp_config, so it shows everything the agent would actually load.
"""
from __future__ import annotations

import json
import os

from src import mcp_config


def _load_raw(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_raw(path: str, data: dict) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return path


def mcp_add(name: str, *, command: str = None, args: list = None, env: dict = None,
            url: str = None, headers: dict = None, path: str = "mcp.json") -> dict:
    """Add or update a server in the config. A `url` makes it remote; otherwise
    it's a stdio `command`. Returns the written entry."""
    data = _load_raw(path)
    servers = data.setdefault("mcpServers", {})
    entry: dict = {}
    if url:
        entry["url"] = url
        if headers:
            entry["headers"] = headers
    else:
        entry["command"] = command or ""
        if args:
            entry["args"] = args
    if env:
        entry["env"] = env
    servers[name] = entry
    _save_raw(path, data)
    return entry


def mcp_remove(name: str, *, path: str = "mcp.json") -> bool:
    """Remove a server by name. Returns True if it existed."""
    data = _load_raw(path)
    servers = data.get("mcpServers", {})
    if name in servers:
        del servers[name]
        _save_raw(path, data)
        return True
    return False


def mcp_list(paths: list = None, cwd: str = None) -> list:
    """The merged server list: [{name, transport, target}] across all sources."""
    servers = mcp_config.load_servers(paths=paths, cwd=cwd)
    rows = []
    for n, c in servers.items():
        target = c.url if c.transport == "http" else (
            " ".join([c.command, *c.args]).strip() if c.command else "")
        rows.append({"name": n, "transport": c.transport, "target": target})
    return rows
