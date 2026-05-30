"""A minimal Language Server Protocol client — code intelligence for the agent.

Tests the protocol + session deterministically over preloaded byte streams (no
real language server required), plus server detection and graceful degradation.
The point: after an edit, korgex can ask a language server for diagnostics
(errors/types) instead of editing blind — and it never crashes when no server
is installed.
"""
from __future__ import annotations

import io
import json

from src.lsp import LspClient, MessageReader, diagnostics, encode_message, server_for


def _framed(*objs) -> io.BytesIO:
    return io.BytesIO(b"".join(encode_message(o) for o in objs))


def test_encode_message_uses_content_length_framing():
    raw = encode_message({"jsonrpc": "2.0", "id": 1, "method": "x"})
    assert raw.startswith(b"Content-Length: ")
    head, body = raw.split(b"\r\n\r\n", 1)
    assert int(head.split(b":")[1]) == len(body)
    assert json.loads(body)["method"] == "x"


def test_message_reader_round_trips_a_stream():
    r = MessageReader(_framed({"a": 1}, {"b": 2}))
    assert r.read() == {"a": 1}
    assert r.read() == {"b": 2}
    assert r.read() is None  # EOF


def test_client_request_matches_response_by_id_and_sends_framed():
    writer = io.BytesIO()
    c = LspClient(_framed({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}), writer)
    assert c.request("initialize", {"rootUri": "file:///x"}) == {"capabilities": {}}
    assert b"initialize" in writer.getvalue()


def test_client_skips_notifications_until_the_matching_response():
    reader = _framed(
        {"jsonrpc": "2.0", "method": "window/logMessage", "params": {}},   # noise first
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
    )
    assert LspClient(reader, io.BytesIO()).request("initialize") == {"ok": True}


def test_poll_diagnostics_returns_published_diagnostics_for_the_uri():
    uri = "file:///x.py"
    reader = _framed({
        "jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
        "params": {"uri": uri, "diagnostics": [
            {"message": "undefined name 'foo'", "severity": 1, "range": {"start": {"line": 3}}}]},
    })
    diags = LspClient(reader, io.BytesIO()).poll_diagnostics(uri)
    assert diags and diags[0]["message"] == "undefined name 'foo'"


def test_server_for_maps_known_extensions_and_none_for_unknown():
    assert server_for("a.py") and server_for("a.ts") and server_for("a.rs") and server_for("a.go")
    assert server_for("a.unknownext") is None


def test_diagnostics_degrades_gracefully_when_no_server_is_available(tmp_path):
    f = tmp_path / "x.unknownlang"
    f.write_text("whatever")
    assert diagnostics(str(f)) == []  # no server for this ext → [] (never raises)
