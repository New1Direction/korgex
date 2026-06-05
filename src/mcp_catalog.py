"""A curated catalog of MCP servers, so adding one is `korgex mcp add <alias>`
instead of remembering npx package names and flags.

Each preset resolves to a plain config dict (command/args/env or url/headers) that
the normal add path writes into mcp.json. Presets here are ones that work with
korgex's current transports (stdio subprocess, or remote with a token/env header).
OAuth-only remotes are intentionally omitted until the device-code flow lands.
"""
from __future__ import annotations

# alias -> preset. `description`/`category`/`needs`/`params` are metadata (shown in
# the catalog); only command/args/env/url/headers become server config. All entries
# below were verified to resolve (npm package exists / uvx package exists / real
# remote URL). npx = Node (stdio), uvx = Python (stdio), url = remote HTTP.
PRESETS = {
    # ── no auth, great defaults ───────────────────────────────────────────────
    "korgex": {
        "description": "korgex's own server — web search/fetch, agent bus, ledger verify/audit (instant)",
        "command": "korgex", "args": ["mcp-server"], "category": "korgex",
    },
    "git": {
        "description": "Local git — status, diff, log, commit, branches (no auth)",
        "command": "uvx", "args": ["mcp-server-git"], "category": "dev",
    },
    "mise": {
        "description": "mise (jdx/mise) — this project's tool versions, env vars + runnable "
                       "tasks (and run_task); mise ships this MCP server for agents",
        "command": "mise", "args": ["mcp"], "needs": ["mise installed"], "category": "dev",
    },
    "context7": {
        "description": "Context7 (Upstash) — up-to-date docs + code examples for any library/"
                       "framework, fetched on demand (hosted; anonymous, API key optional)",
        "url": "https://mcp.context7.com/mcp", "category": "dev",
    },
    "time": {
        "description": "Current time + timezone conversion (no auth)",
        "command": "uvx", "args": ["mcp-server-time"], "category": "utility",
    },
    "fetch": {
        "description": "Fetch a URL as clean markdown (no auth)",
        "command": "uvx", "args": ["mcp-server-fetch"], "category": "web",
    },
    "memory": {
        "description": "Persistent knowledge-graph memory across sessions (no auth)",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"], "category": "memory",
    },
    "sequentialthinking": {
        "description": "A structured step-by-step reasoning scaffold (no auth)",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "category": "reasoning",
    },
    "filesystem": {
        "description": "Read/write files under a directory, sandboxed to {path} (no auth)",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "{path}"],
        "params": {"path": "directory to expose (default: current dir)"}, "category": "files",
    },
    "puppeteer": {
        "description": "Headless-browser automation — navigate, click, screenshot (no auth)",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"], "category": "web",
    },
    "everything": {
        "description": "Reference server — many demo tools/prompts/resources (great for trying MCP)",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"], "category": "demo",
    },
    # ── stdio, need a token/connection ────────────────────────────────────────
    "github": {
        "description": "GitHub — issues, PRs, repos, code search",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}, "needs": ["GITHUB_TOKEN"], "category": "dev",
    },
    "gitlab": {
        "description": "GitLab — issues, MRs, repos",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-gitlab"],
        "env": {"GITLAB_PERSONAL_ACCESS_TOKEN": "${GITLAB_TOKEN}", "GITLAB_API_URL": "${GITLAB_API_URL}"},
        "needs": ["GITLAB_TOKEN"], "category": "dev",
    },
    "postgres": {
        "description": "PostgreSQL — read-only schema inspection + queries",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "${POSTGRES_URL}"],
        "needs": ["POSTGRES_URL"], "category": "data",
    },
    "slack": {
        "description": "Slack — read/post channels and messages",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}", "SLACK_TEAM_ID": "${SLACK_TEAM_ID}"},
        "needs": ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"], "category": "comms",
    },
    "brave-search": {
        "description": "Web + local search via the Brave Search API",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": "${BRAVE_API_KEY}"}, "needs": ["BRAVE_API_KEY"], "category": "web",
    },
    "google-maps": {
        "description": "Google Maps — geocoding, places, directions",
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-google-maps"],
        "env": {"GOOGLE_MAPS_API_KEY": "${GOOGLE_MAPS_API_KEY}"}, "needs": ["GOOGLE_MAPS_API_KEY"],
        "category": "geo",
    },
    # ── remote HTTP (OAuth in the browser; korgex's device-code flow is on the
    # roadmap — these connect, but auth may prompt for a token) ────────────────
    "linear": {
        "description": "Linear — issues & projects (remote, OAuth)",
        "url": "https://mcp.linear.app/mcp", "needs": ["OAuth"], "category": "dev",
    },
    "sentry": {
        "description": "Sentry — errors, issues, releases (remote, OAuth)",
        "url": "https://mcp.sentry.dev/mcp", "needs": ["OAuth"], "category": "observability",
    },
    "mixpanel": {
        "description": "Mixpanel — product analytics queries (remote, OAuth)",
        "url": "https://mcp.mixpanel.com/mcp", "needs": ["OAuth"], "category": "analytics",
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
