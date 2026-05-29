"""
Recall + memory-injection + memory-drift tests (roadmap P2).

korgex was WRITE-ONLY to the ledger — it recorded a causal journal nothing ever
read back, and its memory-injection path was dead code. P2 builds the read side:
1. recall: semantic/substring search over the ledger korgex already writes.
2. memory injection: AGENTS.md + the memory index actually reach the system prompt.
3. memory-drift: a recalled file ref is reconciled against the LIVE workspace
   (content-addressed sha compare) and the decision is an auditable event —
   "trust current state over stale memory", the one thing incumbents punt on.
"""

import hashlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import recall as R  # noqa: E402
from src.agent import KorgexAgent  # noqa: E402
from src.tool_abstraction import route_tool_call  # noqa: E402


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ── 1. load_events (tolerant of array + jsonl) ────────────────────────────

def test_load_events_missing_returns_empty(tmp_path):
    assert R.load_events(str(tmp_path / "nope.json")) == []


def test_load_events_json_array(tmp_path):
    p = tmp_path / "journal.json"
    p.write_text(json.dumps([
        {"seq_id": 1, "tool_name": "Edit", "args": {"file_path": "a.py"}, "result": {}, "success": True},
        {"seq_id": 2, "tool_name": "Bash", "args": {"command": "pytest"}, "result": {}, "success": True},
    ]))
    events = R.load_events(str(p))
    assert len(events) == 2
    assert events[0]["tool_name"] == "Edit"


def test_load_events_jsonl(tmp_path):
    p = tmp_path / "journal.jsonl"
    p.write_text(
        json.dumps({"seq_id": 1, "tool_name": "Read", "args": {"file_path": "x"}, "result": {}}) + "\n"
        + json.dumps({"seq_id": 2, "tool_name": "Write", "args": {"file_path": "y"}, "result": {}}) + "\n"
    )
    events = R.load_events(str(p))
    assert [e["tool_name"] for e in events] == ["Read", "Write"]


# ── 2. event_text + search ────────────────────────────────────────────────

def test_event_text_includes_tool_args_and_result_text():
    ev = {"tool_name": "llm_inference", "args": {"model": "x"},
          "result": {"text": "fixed the oauth refresh on 401"}}
    text = R.event_text(ev).lower()
    assert "llm_inference" in text
    assert "oauth" in text and "401" in text


def test_search_substring_and_of_terms_ranks_match_first():
    events = [
        {"seq_id": 1, "tool_name": "Edit", "args": {"file_path": "auth.py"},
         "result": {"text": "fixed oauth token refresh failing on 401"}},
        {"seq_id": 2, "tool_name": "Bash", "args": {"command": "ls"}, "result": {}},
    ]
    hits = R.search(events, "oauth 401", top_n=5)
    assert hits
    assert hits[0]["event"]["seq_id"] == 1     # the oauth event ranks first
    # the unrelated ls event must not match an AND-of-both-terms query
    assert all("oauth" in R.event_text(h["event"]).lower() for h in hits)


def test_search_respects_top_n():
    events = [{"seq_id": i, "tool_name": "Edit",
               "args": {"file_path": "auth.py"}, "result": {"text": "oauth"}} for i in range(10)]
    assert len(R.search(events, "oauth", top_n=3)) == 3


# ── 3. memory-drift: reconcile recalled ref vs live workspace ─────────────

def test_reconcile_no_drift_when_sha_matches(tmp_path):
    f = tmp_path / "auth.py"
    f.write_text("def login(): ...")
    res = R.reconcile_file_ref("auth.py", _sha("def login(): ..."), str(tmp_path))
    assert res["drift"] is False


def test_reconcile_drift_when_content_changed(tmp_path):
    f = tmp_path / "auth.py"
    f.write_text("def login(): NEW")
    res = R.reconcile_file_ref("auth.py", _sha("def login(): OLD"), str(tmp_path))
    assert res["drift"] is True
    assert "chang" in res["reason"].lower()


def test_reconcile_drift_when_file_gone(tmp_path):
    res = R.reconcile_file_ref("ghost.py", _sha("whatever"), str(tmp_path))
    assert res["drift"] is True
    assert "exist" in res["reason"].lower() or "gone" in res["reason"].lower()


def test_annotate_drift_marks_stale_results(tmp_path):
    (tmp_path / "live.py").write_text("current")
    results = [
        {"event": {"tool_name": "Edit", "args": {"file_path": "live.py"},
                   "result": {"_ref": f"sha256:{_sha('OLD CONTENT')}"}}},
        {"event": {"tool_name": "Bash", "args": {"command": "ls"}, "result": {}}},
    ]
    annotated = R.annotate_drift(results, str(tmp_path))
    # the Edit referenced a file whose content changed → flagged drift
    assert annotated[0]["drift"]["drift"] is True
    # the Bash event has no file ref → no drift annotation (or drift None)
    assert annotated[1].get("drift") in (None, {}, False) or annotated[1]["drift"] is None


# ── 4. Recall tool routes ─────────────────────────────────────────────────

def test_recall_tool_returns_ranked_hits(tmp_path):
    journal = tmp_path / "journal.json"
    journal.write_text(json.dumps([
        {"seq_id": 1, "tool_name": "llm_inference", "args": {},
         "result": {"text": "resolved the oauth 401 by refreshing the token"}},
        {"seq_id": 2, "tool_name": "Bash", "args": {"command": "ls"}, "result": {}},
    ]))
    os.environ["KORG_JOURNAL_PATH"] = str(journal)
    try:
        out = route_tool_call("Recall", {"query": "oauth 401"})
        assert "error" not in out, out
        assert out["count"] >= 1
        assert "oauth" in json.dumps(out["results"]).lower()
    finally:
        del os.environ["KORG_JOURNAL_PATH"]


# ── 5. memory injection into the system prompt ────────────────────────────

class _FakeLedger:
    def record_user_prompt(self, prompt, triggered_by=None):
        return 1

    def record_llm_call(self, **kw):
        return 2

    def record_tool_call(self, **kw):
        return None


def _openai_text(text):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
    )


class _ScriptedAgent(KorgexAgent):
    def __init__(self, responses, **kw):
        kw.setdefault("model", "gpt-4o")
        kw.setdefault("interactive", False)
        super().__init__(**kw)
        self._responses = list(responses)
        self.ledger = _FakeLedger()

    def _get_client(self):
        return object()

    def _call(self, client, messages, tools, output_schema=None, system_prompt=None):
        return self._responses.pop(0)


def test_agents_md_is_injected_into_system_prompt(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# House rules\nAlways run ruff before committing.")
    agent = _ScriptedAgent([_openai_text("ok")], repo_root=str(tmp_path))
    agent.run_task("do something")
    assert "Always run ruff before committing" in agent.system_prompt


def test_system_prompt_defaults_to_base_when_no_agents_md(tmp_path):
    agent = _ScriptedAgent([_openai_text("ok")], repo_root=str(tmp_path))
    agent.run_task("do something")
    assert "Korgex" in agent.system_prompt   # base identity still present
