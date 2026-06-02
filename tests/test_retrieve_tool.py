"""Tests for the Retrieve tool — pull the exact sealed original of something the
model compressed away, by its sha256:.. handle.

Covers both the native handler (src.tools_impl.tool_retrieve_blob) and the
user-facing surface (USER_TOOLS + route_tool_call), proving the whole router
path (param filter + context injection) reaches the handler. Pure + offline:
seal under KORG_BLOB_DIR, no model/network.
"""
from __future__ import annotations

import json

from src import korg_ledger as kl
from src import tool_abstraction as ta
from src import tools_impl


def test_tool_retrieve_blob_round_trips_utf8(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    original = json.dumps({"k": "v" * 2000}).encode("utf-8")
    sha, _ = kl._write_blob(original)

    out = tools_impl.tool_retrieve_blob(ref=f"sha256:{sha}")
    assert out["verified"] is True
    assert out["sha256"] == sha
    assert out["size_bytes"] == len(original)
    assert out["content"].encode("utf-8") == original


def test_tool_retrieve_blob_bare_hex_also_works(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    original = b"plain bytes payload" * 100
    sha, _ = kl._write_blob(original)
    out = tools_impl.tool_retrieve_blob(ref=sha)        # bare hex, no prefix
    assert out["verified"] is True
    assert out["content"].encode("utf-8") == original


def test_tool_retrieve_blob_missing_returns_error_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    out = tools_impl.tool_retrieve_blob(ref="sha256:" + "0" * 64)
    assert "error" in out
    assert "verified" not in out


def test_retrieve_is_registered_as_direct_user_tool():
    assert "Retrieve" in ta.USER_TOOLS
    t = ta.USER_TOOLS["Retrieve"]
    assert t["exposure"] == "direct"
    schema = t["input_schema"]
    assert "ref" in schema["properties"]
    assert "ref" in schema["required"]


def test_route_tool_call_reaches_handler_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    original = json.dumps({"big": ["row"] * 1000}).encode("utf-8")
    sha, _ = kl._write_blob(original)

    out = ta.route_tool_call("Retrieve", {"ref": f"sha256:{sha}"}, repo_root=str(tmp_path))
    assert out["verified"] is True
    assert out["sha256"] == sha
    assert out["content"].encode("utf-8") == original


def test_retrieve_native_handler_registered_in_tool_registry():
    # @register_tool('retrieve_blob', ...) must populate the native registry too.
    from src.tool_base import TOOL_REGISTRY
    assert "retrieve_blob" in TOOL_REGISTRY
