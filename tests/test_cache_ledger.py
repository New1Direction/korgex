"""Verifiable cache: the cache breakdown lands on the llm_inference ledger event.

The agent captures each turn's prompt-cache usage (cache_read / cache_creation /
uncached_input). For a cache hit to be PROVABLE later (`korgex verify`, audit replay,
honest cost), that breakdown has to reach the tamper-evident journal — not just a
behind-a-flag print. These tests pin two things:

  - `_llm_call_args` shapes the inference event's args: the disjoint cache counts are
    folded in WHEN caching is active, and OMITTED on a cold turn so the on-disk shape
    is unchanged for non-cache events (back-compat with older journals + readers).
  - LocalJournalClient (the default offline path) actually writes them through.
"""
import json
from pathlib import Path

from src.korg_ledger import LocalJournalClient, _llm_call_args


# ── the shared arg shaper (used by every record_llm_call transport) ─────────────

def test_llm_args_includes_disjoint_breakdown_when_warm():
    a = _llm_call_args("claude-sonnet-4-6", 900,
                       cache_read_tokens=800, cache_creation_tokens=50,
                       uncached_input_tokens=900)
    assert a == {
        "model": "claude-sonnet-4-6", "prompt_tokens": 900,
        "cache_read_tokens": 800, "cache_creation_tokens": 50,
        "uncached_input_tokens": 900,
    }


def test_llm_args_omits_cache_fields_when_cold():
    # No cache activity → exactly the legacy two-field shape, byte-for-byte.
    assert _llm_call_args("gpt-4o", 500) == {"model": "gpt-4o", "prompt_tokens": 500}
    assert _llm_call_args("gpt-4o", 500, cache_read_tokens=0,
                          cache_creation_tokens=0, uncached_input_tokens=0) == {
        "model": "gpt-4o", "prompt_tokens": 500}


def test_llm_args_creation_only_still_records_breakdown():
    # A cold turn that WRITES the cache has creation>0 / read==0 — still a cache event.
    a = _llm_call_args("claude-sonnet-4-6", 1000,
                       cache_read_tokens=0, cache_creation_tokens=1000,
                       uncached_input_tokens=1000)
    assert a["cache_creation_tokens"] == 1000
    assert a["uncached_input_tokens"] == 1000


# ── LocalJournalClient writes it through to the durable journal ─────────────────

def _read_last_event(path: Path) -> dict:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_local_journal_persists_cache_breakdown(tmp_path):
    jp = tmp_path / "journal.jsonl"
    c = LocalJournalClient(journal_path=str(jp), source_agent="t")
    c.record_llm_call(model="claude-sonnet-4-6", prompt_tokens=900,
                      completion_tokens=10, duration_ms=5, triggered_by=None,
                      cache_read_tokens=800, cache_creation_tokens=50,
                      uncached_input_tokens=900)
    ev = _read_last_event(jp)
    assert ev["tool_name"] == "llm_inference"
    assert ev["args"]["cache_read_tokens"] == 800
    assert ev["args"]["cache_creation_tokens"] == 50
    assert ev["args"]["uncached_input_tokens"] == 900
    assert ev["result"]["completion_tokens"] == 10


def test_local_journal_cold_turn_keeps_legacy_shape(tmp_path):
    jp = tmp_path / "journal.jsonl"
    c = LocalJournalClient(journal_path=str(jp), source_agent="t")
    c.record_llm_call(model="gpt-4o", prompt_tokens=500, completion_tokens=10,
                      duration_ms=5, triggered_by=None)
    ev = _read_last_event(jp)
    assert "cache_read_tokens" not in ev["args"]
    assert "cache_creation_tokens" not in ev["args"]
    assert "uncached_input_tokens" not in ev["args"]
