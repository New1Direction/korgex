"""`korgex mcp add/list/remove` — manage MCP servers without hand-editing JSON.

Writes go to the native project config (mcp.json); list reads the merged view.
"""
import json

from src.mcp_admin import mcp_add, mcp_list, mcp_remove


def test_mcp_add_stdio_writes_config(tmp_path):
    p = tmp_path / "mcp.json"
    mcp_add("fs", command="npx", args=["-y", "srv"], path=str(p))
    data = json.loads(p.read_text())
    assert data["mcpServers"]["fs"]["command"] == "npx"
    assert data["mcpServers"]["fs"]["args"] == ["-y", "srv"]


def test_mcp_add_remote_url_with_headers(tmp_path):
    p = tmp_path / "mcp.json"
    mcp_add("api", url="https://mcp.x/api", headers={"Authorization": "Bearer ${TOK}"}, path=str(p))
    d = json.loads(p.read_text())["mcpServers"]["api"]
    assert d["url"] == "https://mcp.x/api"
    assert d["headers"]["Authorization"] == "Bearer ${TOK}"   # token stays a ref, not resolved on disk


def test_mcp_add_updates_existing_in_place(tmp_path):
    p = tmp_path / "mcp.json"
    mcp_add("fs", command="a", path=str(p))
    mcp_add("fs", command="b", path=str(p))
    servers = json.loads(p.read_text())["mcpServers"]
    assert len(servers) == 1 and servers["fs"]["command"] == "b"


def test_mcp_remove(tmp_path):
    p = tmp_path / "mcp.json"
    mcp_add("fs", command="a", path=str(p))
    assert mcp_remove("fs", path=str(p)) is True
    assert json.loads(p.read_text())["mcpServers"] == {}
    assert mcp_remove("nope", path=str(p)) is False


def test_mcp_list_reports_name_and_transport(tmp_path):
    p = tmp_path / "mcp.json"
    mcp_add("fs", command="npx", path=str(p))
    mcp_add("api", url="https://x", path=str(p))
    rows = {r["name"]: r for r in mcp_list(paths=[str(p)])}
    assert rows["fs"]["transport"] == "stdio"
    assert rows["api"]["transport"] == "http"


def test_cmd_mcp_add_then_list(tmp_path, monkeypatch, capsys):
    # End-to-end through the CLI glue: `korgex mcp add …` writes mcp.json, `list` shows it.
    monkeypatch.chdir(tmp_path)
    from src.cli import cmd_mcp

    monkeypatch.setattr("sys.argv", ["korgex", "mcp", "add", "api", "--url", "https://mcp.x/api"])
    assert cmd_mcp() == 0
    data = json.loads((tmp_path / "mcp.json").read_text())
    assert data["mcpServers"]["api"]["url"] == "https://mcp.x/api"

    monkeypatch.setattr("sys.argv", ["korgex", "mcp", "list"])
    assert cmd_mcp() == 0
    assert "api" in capsys.readouterr().out


def test_cmd_mcp_catalog_lists_presets(monkeypatch, capsys):
    from src.cli import cmd_mcp
    monkeypatch.setattr("sys.argv", ["korgex", "mcp", "catalog"])
    assert cmd_mcp() == 0
    out = capsys.readouterr().out
    assert "everything" in out and "filesystem" in out


def test_cmd_mcp_add_from_catalog_alias(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.cli import cmd_mcp
    monkeypatch.setattr("sys.argv", ["korgex", "mcp", "add", "everything"])
    assert cmd_mcp() == 0
    d = json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["everything"]
    assert d["command"] == "npx" and any("server-everything" in a for a in d["args"])
