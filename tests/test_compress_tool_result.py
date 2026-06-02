"""LOAD-BEARING tests for verifiable tool-output compression in the agent loop.

The HARD INVARIANT is NEVER LOSE DATA. _compress_tool_result only rewrites the
MODEL'S view of a large tool result; the full original is sealed (content-ref,
hash-chained) and Retrieve returns it byte-for-byte, sha256-verified. The
mandatory test is the round-trip: seal -> compact view -> Retrieve == original
bytes (asserted against korg_ledger._canonical_bytes of the original).

Also covers the safety guards: never compress small results, never compress
error/control results, env-disable, fail-safe on a seal hiccup, and that a
context.compress ledger fact is recorded (chained triggered_by=llm_seq).

Pure + offline: a real LocalJournalClient on a tmp journal + KORG_BLOB_DIR; no
model/network. The Agent is built only to reach the pure helper.
"""
from __future__ import annotations

import json

from src import korg_ledger as kl
from src import tools_impl
from src.agent import KorgexAgent
from src.korg_ledger import LocalJournalClient


class CountingLedger:
    """Records events in-memory so we can assert WHICH ledger facts were written
    (and that none are written on the small/error/disabled paths)."""

    def __init__(self):
        self.events = []

    def record_tool_call(self, **kw):
        self.events.append(kw)
        return len(self.events)

    def record_user_prompt(self, prompt, triggered_by=None):
        return 0

    def record_llm_call(self, **kw):
        return 0


def _agent(tmp_path):
    return KorgexAgent(repo_root=str(tmp_path), interactive=False)


def _load_journal_events(path):
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ── THE MANDATORY LOSSLESS ROUND-TRIP ───────────────────────────────────────

def test_round_trip_text_result_is_byte_for_byte(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    journal = tmp_path / "journal.jsonl"
    korg = LocalJournalClient(journal_path=str(journal))
    agent = _agent(tmp_path)

    original = {"output": "X" * 20000, "exit_code": 0}
    compact = agent._compress_tool_result(original, korg, llm_seq=1, tool_name="Bash")

    assert compact["_compressed"] is True
    assert compact["_ref"].startswith("sha256:")
    # The model-facing dict is much smaller than the original:
    assert len(json.dumps(compact)) < len(json.dumps(original))
    # The view is a short str:
    assert isinstance(compact["view"], str)
    # LOSSLESS: the sealed blob equals the canonical bytes of the original, and
    # Retrieve hands back exactly those bytes.
    sealed = kl.read_blob(compact["_ref"])
    assert sealed == kl._canonical_bytes(original)
    got = tools_impl.tool_retrieve_blob(ref=compact["_ref"])
    assert got["verified"] is True
    assert json.loads(got["content"]) == original


def test_round_trip_big_nested_json(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    korg = LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"))
    agent = _agent(tmp_path)

    original = {"rows": [{"i": i, "v": "z" * 30} for i in range(500)]}
    compact = agent._compress_tool_result(original, korg, llm_seq=2, tool_name="Grep")
    assert compact["_compressed"] is True
    assert kl.read_blob(compact["_ref"]) == kl._canonical_bytes(original)
    got = tools_impl.tool_retrieve_blob(ref=compact["_ref"])
    assert json.loads(got["content"]) == original


def test_round_trip_big_python_source(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    korg = LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"))
    agent = _agent(tmp_path)

    py = "def f(x):\n    return x + 1\n\n" * 600
    original = {"content": py, "filepath": "big.py"}
    compact = agent._compress_tool_result(original, korg, llm_seq=3, tool_name="Read")
    assert compact["_compressed"] is True
    assert kl.read_blob(compact["_ref"]) == kl._canonical_bytes(original)
    assert tools_impl.tool_retrieve_blob(ref=compact["_ref"])["content"]


# ── SAFETY GUARDS ───────────────────────────────────────────────────────────

def test_small_result_unchanged_no_ledger_fact(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    korg = CountingLedger()
    agent = _agent(tmp_path)

    small = {"ok": True, "value": 7}
    out = agent._compress_tool_result(small, korg, llm_seq=1, tool_name="Bash")
    assert out is small                       # identity, untouched
    assert "_compressed" not in out
    assert korg.events == []                   # no context.compress fact


def test_env_zero_disables_compression(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("KORGEX_COMPRESS_THRESHOLD", "0")
    korg = CountingLedger()
    agent = _agent(tmp_path)

    huge = {"output": "Y" * 50000}
    out = agent._compress_tool_result(huge, korg, llm_seq=1, tool_name="Bash")
    assert out is huge
    assert korg.events == []


def test_error_result_is_not_compressed(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    korg = CountingLedger()
    agent = _agent(tmp_path)

    err = {"error": "boom", "output": "X" * 20000}
    out = agent._compress_tool_result(err, korg, llm_seq=1, tool_name="Bash")
    assert out is err                          # errors must reach the model intact
    assert korg.events == []


def test_already_compressed_is_not_recompressed(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    korg = CountingLedger()
    agent = _agent(tmp_path)

    already = {"_compressed": True, "_ref": "sha256:" + "a" * 64, "view": "x" * 30000}
    out = agent._compress_tool_result(already, korg, llm_seq=1, tool_name="Bash")
    assert out is already
    assert korg.events == []


def test_fail_safe_when_seal_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    korg = CountingLedger()
    agent = _agent(tmp_path)

    def _boom(_data):
        raise OSError("disk full")
    monkeypatch.setattr(kl, "_write_blob", _boom)

    original = {"output": "X" * 20000}
    out = agent._compress_tool_result(original, korg, llm_seq=1, tool_name="Bash")
    assert out is original                     # fail safe: original returned, loop lives
    assert korg.events == []


def test_view_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    korg = LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"))
    agent = _agent(tmp_path)

    secret = "sk-ant-" + "A" * 40
    original = {"output": ("line with " + secret + "\n") + "filler\n" * 5000}
    compact = agent._compress_tool_result(original, korg, llm_seq=1, tool_name="Bash")
    assert secret not in compact["view"]       # model view carries no secret
    # The credential must NOT reach the (shareable) blob store either: we seal the
    # REDACTED result, never the raw secret bytes.
    blob = kl.read_blob(compact["_ref"])
    assert secret.encode() not in blob
    # ...but no data is lost — the blob IS the redacted original, byte-for-byte, so
    # Retrieve still returns the faithful (redacted) result, sha256-verified.
    from src.sanitize import redact
    assert blob == kl._canonical_bytes(redact(original))


def test_retrieve_result_is_never_recompressed(tmp_path, monkeypatch):
    # REGRESSION (found dogfooding korgex on the wire): the model calls Retrieve to
    # pull the full deferred bytes back; if THAT result is fed through
    # _compress_tool_result it gets re-sealed into another compact view, so the
    # model never actually receives the content — it loops Retrieve -> view ->
    # Retrieve until it stalls. Retrieve's whole job is to UNDO compression, so its
    # output must be exempt and reach the model verbatim.
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    agent = _agent(tmp_path)
    korg = CountingLedger()
    retrieved = {"verified": True, "sha256": "ab" * 32, "size_bytes": 60000,
                 "content": {"output": "X" * 60000}}  # big, well over threshold
    out = agent._compress_tool_result(retrieved, korg, llm_seq=1, tool_name="Retrieve")
    assert out is retrieved          # full bytes reach the model, verbatim
    assert "_compressed" not in out  # not wrapped into another view
    assert korg.events == []         # nothing re-sealed


def test_context_compress_ledger_fact_fields_and_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    journal = tmp_path / "journal.jsonl"
    korg = LocalJournalClient(journal_path=str(journal))
    agent = _agent(tmp_path)

    original = {"output": "Q" * 20000}
    compact = agent._compress_tool_result(original, korg, llm_seq=42, tool_name="Bash")

    events = _load_journal_events(journal)
    comp = [e for e in events if e["tool_name"] == "context.compress"]
    assert len(comp) == 1
    ev = comp[0]
    assert ev["triggered_by"] == 42
    res = ev["result"]
    assert res["original_sha256"] == compact["original_sha256"]
    assert res["original_size"] >= 20000
    assert "compressed_size" in res
    assert 0 < res["ratio"] < 1               # we actually shrank it
    assert ev["args"]["tool"] == "Bash"


def test_compression_emits_no_data_loss_marker_in_view(tmp_path, monkeypatch):
    # The compact dict must point the model at Retrieve so it knows the full
    # original is recoverable (UX of the invariant, not just the mechanics).
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)
    korg = LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"))
    agent = _agent(tmp_path)
    compact = agent._compress_tool_result({"output": "X" * 20000}, korg, llm_seq=1, tool_name="Bash")
    assert "Retrieve" in compact.get("hint", "")


# ── INTEGRATION: compression is wired into the serial model-facing append ────

class _CaptureLedger(CountingLedger):
    pass


def _script_llm(agent, scripted_rounds):
    """Replay (tool_calls, text) rounds AND capture the `messages` the model is
    shown each round, so we can assert what the compressed tool result looks
    like by the time it reaches the next inference."""
    rounds = iter(scripted_rounds)
    captured = {"messages": []}

    def _call(client, messages, *a, **k):
        # Snapshot the transcript the model sees at this round.
        captured["messages"].append(list(messages))
        return next(rounds)

    agent._get_client = lambda: object()
    agent._call = _call
    agent._extract_tool_calls = lambda resp: resp[0]
    agent._extract_final_text = lambda resp: resp[1]
    agent._assistant_turn = lambda resp: {"role": "assistant", "content": resp[1]}
    return captured


def test_serial_loop_appends_compressed_view_and_ledger_keeps_original(tmp_path, monkeypatch):
    """One real tool round through run_task: the model-facing message carries
    the compact view + _ref, while the ledger holds BOTH the full original
    tool event (pre-compress) AND a context.compress event."""
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.delenv("KORGEX_COMPRESS_THRESHOLD", raising=False)

    # A real file the Read tool will return at full size (well over threshold).
    big = tmp_path / "big.txt"
    big.write_text("LINE\n" * 8000)

    a = _agent(tmp_path)
    a.edit_policy = "session"
    a.ledger = _CaptureLedger()

    read_call = [{"id": "t1", "name": "Read", "args": {"file_path": str(big)}}]
    captured = _script_llm(a, [(read_call, ""), ([], "done")])

    a.run_task("read the big file")

    # Round 2's transcript includes the tool result the model now sees.
    round2_msgs = captured["messages"][-1]
    blob = json.dumps(round2_msgs)
    assert "_compressed" in blob and "_ref" in blob
    # The raw 8000-line body must NOT be in the model's view anymore.
    assert "LINE\nLINE\nLINE\nLINE\nLINE" not in blob

    # Ledger: the full original Read event was recorded (pre-compress) AND a
    # context.compress event exists.
    names = [e["tool_name"] for e in a.ledger.events]
    assert "Read" in names
    assert "context.compress" in names


def test_compression_wired_before_both_model_facing_appends():
    """Both append sites (serial + parallel Agent post-pass) must compress the
    tool result BEFORE it becomes a model-facing message. A source-structure
    guard so a refactor can't silently drop the parallel-path insertion (which
    is harder to drive end-to-end than the serial path)."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "agent.py"
    text = src.read_text()
    append_sites = [
        m.start() for m in re.finditer(
            r'messages\.append\(self\._tool_result_turn\(call\["id"\], tool_result\)\)', text)
    ]
    # Exactly the two known model-facing append sites for a dispatched tool_result.
    assert len(append_sites) == 2, append_sites
    for site in append_sites:
        # The compression call must appear in the ~400 chars immediately before
        # the append (same block), guarding both insertion points.
        window = text[max(0, site - 400):site]
        assert "self._compress_tool_result(" in window, (
            "compression must run before this model-facing append")
