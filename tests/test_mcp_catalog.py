"""A curated MCP server catalog — `korgex mcp add <alias>` instead of remembering
npx incantations. Presets resolve to a config dict the existing add path writes.
"""
from src.mcp_catalog import entries, resolve


def test_resolve_known_stdio_preset():
    cfg = resolve("everything")
    assert cfg["command"] == "npx"
    assert any("server-everything" in a for a in cfg["args"])


def test_resolve_unknown_is_none():
    assert resolve("does-not-exist") is None


def test_resolve_fills_path_placeholder_for_filesystem():
    cfg = resolve("filesystem", path_value="/home/u/proj")
    assert "/home/u/proj" in cfg["args"]
    assert "{path}" not in " ".join(cfg["args"])


def test_resolve_filesystem_defaults_path_when_absent():
    cfg = resolve("filesystem")
    # no placeholder left dangling even without a path
    assert "{path}" not in " ".join(cfg["args"])


def test_resolve_token_preset_carries_env_or_header():
    gh = resolve("github")
    assert "env" in gh and "GITHUB_TOKEN" in gh["env"]


def test_resolve_strips_metadata_keys():
    cfg = resolve("everything")
    # only real server-config keys survive (no description/category/needs)
    assert set(cfg).issubset({"command", "args", "env", "url", "headers"})


def test_entries_lists_aliases_with_transport_and_needs():
    rows = {e["alias"]: e for e in entries()}
    assert "everything" in rows and rows["everything"]["transport"] == "stdio"
    assert "korgex" in rows                       # our own server is in the catalog
    assert isinstance(rows["github"]["needs"], list) and "GITHUB_TOKEN" in rows["github"]["needs"]


def test_catalog_is_richly_populated():
    rows = {e["alias"]: e for e in entries()}
    # the canonical servers every agent uses, across transports
    for alias in ("git", "postgres", "slack", "memory", "time", "linear", "sentry"):
        assert alias in rows, f"{alias} missing from catalog"
    assert len(rows) >= 16


def test_remote_presets_are_http():
    rows = {e["alias"]: e for e in entries()}
    assert rows["linear"]["transport"] == "http"
    assert rows["sentry"]["transport"] == "http"


def test_resolve_uvx_server():
    cfg = resolve("git")
    assert cfg["command"] == "uvx" and "mcp-server-git" in cfg["args"]
