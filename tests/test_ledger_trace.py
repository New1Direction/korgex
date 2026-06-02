"""Tests for the cognition trace (src/ledger_trace.py).

korgex's differentiator is a hash-chained, tamper-evident ledger of its own
cognition. This makes it LEGIBLE: reconstruct the causal DAG every event already
carries (each has seq_id + triggered_by — user_prompt → llm_inference rounds →
the tool_calls that round requested) and render a readable tree of what the agent
did and what caused it. Pure (events in → tree/text out), so it tests offline; the
trace is trustworthy because `korgex verify` proves the chain wasn't edited.
"""
from src import ledger_trace as LT


def _evts():
    # A realistic mini-journal: prompt → llm round → Read + Edit → second llm round.
    return [
        {"seq_id": 1, "tool_name": "user_prompt", "args": {"prompt": "fix the auth bug"},
         "triggered_by": None},
        {"seq_id": 2, "tool_name": "llm_inference", "args": {"model": "gpt-4o", "prompt_tokens": 900},
         "result": {"completion_tokens": 40}, "success": True, "triggered_by": 1},
        {"seq_id": 3, "tool_name": "Read", "args": {"file_path": "src/auth.py"},
         "success": True, "duration_ms": 5, "triggered_by": 2},
        {"seq_id": 4, "tool_name": "Edit", "args": {"file_path": "src/auth.py"},
         "success": True, "duration_ms": 12, "triggered_by": 2},
        {"seq_id": 5, "tool_name": "llm_inference", "args": {"model": "gpt-4o"},
         "success": True, "triggered_by": 2},
    ]


class TestBuildForest:
    def test_one_root_with_causal_children(self):
        forest = LT.build_forest(_evts())
        assert len(forest) == 1
        root = forest[0]
        assert root["seq_id"] == 1
        # the llm_inference (seq 2) hangs off the prompt
        llm = [c for c in root["children"] if c["seq_id"] == 2][0]
        # the Read + Edit + 2nd llm all triggered_by=2 → children of the llm round
        child_seqs = sorted(c["seq_id"] for c in llm["children"])
        assert child_seqs == [3, 4, 5]

    def test_multiple_prompts_make_multiple_roots(self):
        evts = _evts() + [
            {"seq_id": 6, "tool_name": "user_prompt", "args": {"prompt": "now add a test"},
             "triggered_by": None},
            {"seq_id": 7, "tool_name": "llm_inference", "args": {"model": "gpt-4o"},
             "triggered_by": 6},
        ]
        forest = LT.build_forest(evts)
        assert [r["seq_id"] for r in forest] == [1, 6]

    def test_orphan_event_does_not_crash(self):
        # triggered_by points at a seq that isn't present → surfaced, never dropped/crashed
        evts = [{"seq_id": 9, "tool_name": "Edit", "args": {"file_path": "x"}, "triggered_by": 999}]
        forest = LT.build_forest(evts)
        assert len(forest) == 1            # treated as a root rather than lost

    def test_empty(self):
        assert LT.build_forest([]) == []


class TestRenderTrace:
    def test_renders_prompt_thinking_and_tools(self):
        out = LT.render_trace(_evts(), color=False)
        assert "fix the auth bug" in out          # the prompt
        assert "Read" in out and "Edit" in out     # the tools
        assert "src/auth.py" in out                # the target
        assert "gpt-4o" in out                     # the model that thought

    def test_shows_success_markers(self):
        evts = _evts() + [{"seq_id": 6, "tool_name": "Bash", "args": {"command": "pytest"},
                           "success": False, "duration_ms": 30, "triggered_by": 2}]
        out = LT.render_trace(evts, color=False)
        assert "✓" in out and "✗" in out           # ok and failed calls both marked

    def test_empty_is_friendly(self):
        out = LT.render_trace([], color=False)
        assert out == "" or "no" in out.lower()


class TestExplainWhy:
    def test_traces_a_touched_file_back_to_the_prompt(self):
        out = LT.explain_why(_evts(), "src/auth.py", color=False)
        assert "fix the auth bug" in out           # the originating prompt
        assert "Edit" in out and "src/auth.py" in out

    def test_substring_match_on_a_partial_path(self):
        out = LT.explain_why(_evts(), "auth.py", color=False)   # partial
        assert "src/auth.py" in out

    def test_untouched_target_is_reported_clearly(self):
        out = LT.explain_why(_evts(), "nope.py", color=False)
        assert "nope.py" in out and "no" in out.lower()

    def test_causal_path_runs_root_to_touch(self):
        # the path is the originating prompt → the thinking → the touch, in order
        by_seq = {e["seq_id"]: e for e in _evts()}
        path = LT.causal_path(by_seq, 4)           # the Edit
        assert [e["seq_id"] for e in path] == [1, 2, 4]
