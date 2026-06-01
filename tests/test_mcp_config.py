"""Multi-source MCP config: remote (HTTP) + stdio servers, ${ENV} interpolation,
vendor-compat config files merged by name, per-tool timeouts.

korgex was stdio + single-file (mcp.json) only. These pin the new surface: a `url`
makes a server remote (transport=http), headers carry auth (Bearer ${TOKEN}),
sibling vendors' configs are read and merged (native wins), and timeouts are tunable.
"""
import json

from src.mcp_config import default_sources, interpolate, load_servers, parse_server


def test_interpolate_expands_env_and_default():
    env = {"FOO": "bar"}
    assert interpolate("${FOO}", env) == "bar"
    assert interpolate("x-${FOO}-y", env) == "x-bar-y"
    assert interpolate("${MISSING:-def}", env) == "def"
    assert interpolate("${MISSING}", env) == ""   # unset, no default → empty
    assert interpolate("plain", env) == "plain"


def test_parse_server_stdio():
    c = parse_server("fs", {"command": "npx", "args": ["-y", "srv"]}, env={})
    assert c.transport == "stdio" and c.command == "npx" and c.args == ["-y", "srv"]


def test_parse_server_http_inferred_from_url():
    c = parse_server("api", {"url": "https://mcp.example.com/mcp"}, env={})
    assert c.transport == "http"
    assert c.url == "https://mcp.example.com/mcp"


def test_parse_server_explicit_type_http():
    assert parse_server("api", {"type": "http", "url": "https://x"}, env={}).transport == "http"


def test_parse_server_interpolates_url_headers_args_env():
    env = {"TOK": "secret", "TMP": "/t"}
    c = parse_server("api", {
        "url": "https://x/${TMP}",
        "headers": {"Authorization": "Bearer ${TOK}"},
    }, env=env)
    assert c.url == "https://x//t"
    assert c.headers["Authorization"] == "Bearer secret"
    s = parse_server("fs", {"command": "npx", "args": ["${TMP}"], "env": {"K": "${TOK}"}}, env=env)
    assert s.args == ["/t"] and s.env["K"] == "secret"


def test_parse_server_tool_timeouts_and_startup():
    c = parse_server("api", {"url": "https://x", "tool_timeouts": {"slow": 300},
                             "startup_timeout_sec": 15}, env={})
    assert c.tool_timeouts == {"slow": 300}
    assert c.startup_timeout == 15


def test_load_servers_merges_sources_native_wins(tmp_path):
    native = tmp_path / ".mcp.json"
    native.write_text(json.dumps({"mcpServers": {"a": {"url": "https://native"}}}))
    compat = tmp_path / ".claude" / "mcp.json"
    compat.parent.mkdir()
    compat.write_text(json.dumps({"mcpServers": {"a": {"url": "https://compat"},
                                                 "b": {"command": "x"}}}))
    servers = load_servers(paths=[str(native), str(compat)], env={})
    assert servers["a"].url == "https://native"   # first (native) source wins
    assert "b" in servers                          # and compat-only servers are still picked up


def test_default_sources_lists_native_before_vendor_compat(tmp_path):
    srcs = default_sources(str(tmp_path))
    names = [s.replace(str(tmp_path), "").lstrip("/") for s in srcs]
    assert names.index("mcp.json") < names.index(".claude/mcp.json")
    assert ".cursor/mcp.json" in names
