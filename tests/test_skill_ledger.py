"""Verifiable self-improvement — the agent's skill self-modifications must land in
the korg-ledger as first-class, causally-linked events, not silent background writes.

korgex's whole premise is a verifiable cognition ledger; the one place the agent
modifies *itself* (learning / merging / aging its own skills) was the one place with
no audit trail. These pin the event shapes + the reader that `/skills log` and
`korgex why`/`trace` consume.
"""
from __future__ import annotations

from src import skill_ledger as SL


class _FakeClient:
    """Captures record_tool_call calls the way the real ledger clients receive them
    (all-keyword, returns a synchronous seq_id)."""

    def __init__(self):
        self.calls = []
        self._seq = 0

    def record_tool_call(self, *, tool_name, args, result, success, duration_ms, triggered_by=None):
        self._seq += 1
        self.calls.append({
            "tool_name": tool_name, "args": args, "result": result,
            "success": success, "duration_ms": duration_ms, "triggered_by": triggered_by,
            "seq_id": self._seq,
        })
        return self._seq


def test_record_learned_emits_a_causal_skill_event():
    c = _FakeClient()
    seq = SL.record_learned(c, name="run-tests", action="create",
                            description="run the suite", reason="did it twice", triggered_by=7)
    assert seq == 1
    call = c.calls[0]
    assert call["tool_name"] == "skill.learned"
    assert call["args"]["name"] == "run-tests"
    assert call["args"]["action"] == "create"
    assert call["result"]["reason"] == "did it twice"
    assert call["triggered_by"] == 7          # chained to the turn that taught it
    assert call["success"] is True


def test_record_learned_update_uses_the_updated_event():
    c = _FakeClient()
    SL.record_learned(c, name="run-tests", action="update", triggered_by=3)
    assert c.calls[0]["tool_name"] == "skill.updated"


def test_record_curated_records_merges_and_removals():
    c = _FakeClient()
    SL.record_curated(c, merged=["a"], removed=["b", "c"], reason="dupes", triggered_by=9)
    call = c.calls[0]
    assert call["tool_name"] == "skill.curated"
    assert call["args"]["merged"] == ["a"]
    assert call["args"]["removed"] == ["b", "c"]
    assert call["triggered_by"] == 9
    assert call["success"] is True


def test_record_swept_flattens_transitions():
    c = _FakeClient()
    SL.record_swept(c, transitions=[("x", "active", "stale"), ("y", "stale", "archived")])
    call = c.calls[0]
    assert call["tool_name"] == "skill.swept"
    assert call["args"]["count"] == 2
    assert {"name": "x", "from": "active", "to": "stale"} in call["args"]["transitions"]


def test_record_review_failed_is_a_failure_verdict():
    # The whole point: a failed self-improvement pass is RECORDED, not swallowed.
    c = _FakeClient()
    SL.record_review_failed(c, error=ValueError("boom"), phase="curate", triggered_by=2)
    call = c.calls[0]
    assert call["tool_name"] == "skill.review_failed"
    assert call["success"] is False           # tamper-evident failure in the chain
    assert "boom" in call["result"]["error"]
    assert call["args"]["phase"] == "curate"


def test_recording_never_raises_on_a_broken_client():
    class Broken:
        def record_tool_call(self, **k):
            raise RuntimeError("ledger down")
    # learning is best-effort; a ledger failure must never crash the caller
    assert SL.record_learned(Broken(), name="x", action="create") is None
    assert SL.record_review_failed(Broken(), error="e") is None


def test_record_handles_none_client():
    assert SL.record_learned(None, name="x", action="create") is None
    assert SL.record_curated(None, merged=[], removed=[]) is None


def test_skill_log_filters_journal_to_skill_events():
    events = [
        {"seq_id": 1, "tool_name": "user_prompt", "args": {"prompt": "hi"}, "result": {}},
        {"seq_id": 2, "tool_name": "Edit", "args": {}, "result": {}},
        {"seq_id": 3, "tool_name": "skill.learned", "args": {"name": "run-tests", "action": "create"},
         "result": {"reason": "twice"}, "triggered_by": 1},
        {"seq_id": 4, "tool_name": "skill.curated", "args": {"merged": ["a"], "removed": ["b"]},
         "result": {}, "triggered_by": 1},
    ]
    rows = SL.skill_log(events)
    assert [r["event"] for r in rows] == ["skill.learned", "skill.curated"]
    assert rows[0]["name"] == "run-tests"
    assert rows[0]["seq"] == 3
    assert rows[0]["triggered_by"] == 1
    assert rows[1]["args"]["removed"] == ["b"]


def test_skill_log_empty_on_no_events():
    assert SL.skill_log([]) == []
    assert SL.skill_log(None) == []


def test_format_row_renders_each_kind():
    # One-line human rendering shared by `/skills log` (REPL) and `korgex skills log` (CLI).
    learned = SL.format_row({"event": "skill.learned", "seq": 2, "name": "run-suite",
                             "result": {"reason": "ran it twice"}})
    assert "learned: run-suite" in learned
    assert "ran it twice" in learned                       # the WHY is surfaced

    curated = SL.format_row({"event": "skill.curated", "seq": 3,
                             "args": {"merged": ["a"], "removed": ["b", "c"]}})
    assert "kept a" in curated and "removed b, c" in curated

    failed = SL.format_row({"event": "skill.review_failed", "seq": 4,
                            "args": {"phase": "curate"}, "result": {"error": "boom"}})
    assert "FAILED" in failed and "curate" in failed and "boom" in failed

    swept = SL.format_row({"event": "skill.swept", "seq": 5, "args": {"count": 2}})
    assert "swept" in swept and "2" in swept
