"""A curated catalog of MCP servers, so adding one is `korgex mcp add <alias>`
instead of remembering npx package names and flags.

Each preset resolves to a plain config dict (command/args/env or url/headers) that
the normal add path writes into mcp.json. Presets here are ones that work with
korgex's current transports (stdio subprocess, or remote with a token/env header).
OAuth-only remotes are intentionally omitted until the device-code flow lands.
"""
from __future__ import annotations

# alias -> preset. `description`/`category`/`needs`/`params` are metadata (shown in
# the catalog); only command/args/env/url/headers become server config.
PRESETS = {
    "korgex": {
        "description": "korgex's own ledger server — verify/audit/import (instant, no deps)",
        "command": "korgex", "args": ["mcp-server"], "category": "korgex",
    },
    "everything": {
        "description": "Reference server — many demo tools, prompts & resources (great for trying MCP)",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"], "category": "demo",
    },
    "filesystem": {
        "description": "Read/write files under a directory (sandboxed to {path})",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{path}"],
        "params": {"path": "directory to expose (default: current dir)"}, "category": "files",
    },
    "memory": {
        "description": "Persistent knowledge-graph memory across sessions",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"], "category": "memory",
    },
    "sequentialthinking": {
        "description": "A structured step-by-step reasoning scaffold",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "category": "reasoning",
    },
    "github": {
        "description": "GitHub — issues, PRs, repos, code search (needs a token)",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}, "needs": ["GITHUB_TOKEN"], "category": "dev",
    },
}

_CONFIG_KEYS = ("command", "args", "env", "url", "headers")


def resolve(alias: str, path_value: str = None) -> dict:
    """A preset's server-config dict (only command/args/env/url/headers), with the
    ``{path}`` placeholder filled. Returns None for an unknown alias."""
    p = PRESETS.get(alias)
    if not p:
        return None
    cfg = {k: v for k, v in p.items() if k in _CONFIG_KEYS}
    fill = path_value or "."
    if "args" in cfg:
        cfg["args"] = [a.replace("{path}", fill) for a in cfg["args"]]
    return cfg


def entries() -> list:
    """Catalog rows: [{alias, description, transport, needs, category}]."""
    rows = []
    for alias, p in PRESETS.items():
        rows.append({
            "alias": alias,
            "description": p.get("description", ""),
            "transport": "http" if p.get("url") else "stdio",
            "needs": p.get("needs", []),
            "category": p.get("category", ""),
        })
    return rows
