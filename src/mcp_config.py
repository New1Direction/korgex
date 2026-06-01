"""Multi-source MCP server configuration.

korgex began stdio-only and single-file (mcp.json). This layer adds the modern
shape:
  * **remote servers** — a server with a ``url`` (and no ``command``) is HTTP, no
    subprocess; ``type``/``transport`` of http/sse/streamable-http also works.
  * **auth** — ``headers`` (e.g. ``Authorization: Bearer ${TOKEN}``) for remote
    servers, with ``${ENV}`` / ``${ENV:-default}`` interpolation everywhere.
  * **vendor-compat** — read sibling agents' config files (``.claude/mcp.json``,
    ``.cursor/mcp.json``) so a user migrating in doesn't reconfigure; servers are
    merged by name and the higher-priority (native) source wins.
  * **timeouts** — per-tool overrides + a startup timeout.

Pure and dependency-light so it's fully testable; the client/router consume the
``MCPServerConfig`` objects it returns.
"""
from __future__ import annotations

import json
import os
import re

from src.mcp_client import MCPServerConfig

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def interpolate(value: str, env=None) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}`` against env (os.environ if None).
    An unset var with no default becomes the empty string."""
    if not isinstance(value, str):
        return value
    e = os.environ if env is None else env

    def _repl(m):
        var, default = m.group(1), m.group(2)
        if e.get(var):
            return e[var]
        return default if default is not None else ""
    return _VAR.sub(_repl, value)


def _interp_deep(obj, env):
    if isinstance(obj, str):
        return interpolate(obj, env)
    if isinstance(obj, list):
        return [_interp_deep(x, env) for x in obj]
    if isinstance(obj, dict):
        return {k: _interp_deep(v, env) for k, v in obj.items()}
    return obj


def parse_server(name: str, cfg: dict, env=None) -> MCPServerConfig:
    """Build an MCPServerConfig from one server's raw config dict.

    Transport is inferred: http if ``type``/``transport`` says so, or if a ``url``
    is present without a ``command``; otherwise stdio. All string values are
    ${ENV}-interpolated first.
    """
    cfg = _interp_deep(dict(cfg or {}), env)
    url = cfg.get("url")
    declared = (cfg.get("type") or cfg.get("transport") or "").lower()
    is_http = declared in ("http", "sse", "streamable-http") or (bool(url) and not cfg.get("command"))
    return MCPServerConfig(
        name=name,
        command=cfg.get("command", "") or "",
        args=list(cfg.get("args", []) or []),
        env=dict(cfg.get("env", {}) or {}),
        transport="http" if is_http else "stdio",
        url=url,
        timeout=int(cfg.get("timeout", 60) or 60),
        headers=dict(cfg.get("headers", {}) or {}),
        tool_timeouts={k: int(v) for k, v in (cfg.get("tool_timeouts", {}) or {}).items()},
        startup_timeout=int(cfg.get("startup_timeout_sec", cfg.get("startup_timeout", 30)) or 30),
    )


def default_sources(cwd: str = None) -> list:
    """Candidate config files in PRIORITY order (first wins): project-native,
    then project vendor-compat, then global."""
    cwd = cwd or os.getcwd()
    home = os.path.expanduser("~")
    return [
        os.path.join(cwd, "mcp.json"),
        os.path.join(cwd, ".mcp.json"),
        os.path.join(cwd, ".claude", "mcp.json"),
        os.path.join(cwd, ".cursor", "mcp.json"),
        os.path.join(home, ".korgex", "mcp.json"),
    ]


def _read(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("mcpServers") or data.get("servers") or {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def load_servers(paths=None, cwd: str = None, env=None) -> dict:
    """Merge MCP servers from all sources by name. Sources are in priority order;
    the FIRST source to define a name wins (later duplicates are ignored)."""
    paths = paths if paths is not None else default_sources(cwd)
    out: dict = {}
    for p in paths:
        for name, cfg in _read(p).items():
            if name not in out:
                out[name] = parse_server(name, cfg, env=env)
    return out
