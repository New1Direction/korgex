import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from src import korg_ledger as KL

def test_build_body_redacts_before_content_ref():
    # a fake AWS key shape must be masked in the assembled body
    body, refs = KL._build_body(
        "Bash", {"command": "echo AKIAIOSFODNN7EXAMPLE"}, {}, True, 0, None, "tester")
    assert "AKIAIOSFODNN7EXAMPLE" not in repr(body)
    assert body["tool_name"] == "Bash"
    assert body["success"] is True and "seq_id" not in body

def test_http_record_tool_call_redacts(monkeypatch):
    # the HTTP path historically skipped redaction; assert it no longer does.
    enqueued = {}
    c = KL.KorgLedgerClient.__new__(KL.KorgLedgerClient)
    c.source_agent = "tester"
    monkeypatch.setattr(c, "_is_available", lambda: True)
    class _W:
        def enqueue(self, body): enqueued.update(body)
    monkeypatch.setattr(c, "_get_writer", lambda: _W())
    c.record_tool_call("Bash", {"command": "x AKIAIOSFODNN7EXAMPLE y"}, {}, True, 0)
    assert "AKIAIOSFODNN7EXAMPLE" not in repr(enqueued)
