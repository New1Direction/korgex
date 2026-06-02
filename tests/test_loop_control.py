"""Tests for autonomous-loop control (src/loop_control.py).

`/loop` lets korgex grind through a task list unattended: after a turn ends, if
open tasks remain it auto-continues, until everything's done or a hard cap stops
it (so a confused model can't burn the API key forever). The CONTINUE decision is
the safety-critical part, so it's a pure function tested exhaustively here; the
REPL just drives it.
"""
import io

from src import loop_control as L
from src.repl import Repl


class TestShouldContinue:
    def test_continues_while_open_tasks_remain(self):
        go, _ = L.should_continue(enabled=True, open_tasks=3, iterations=1, max_iterations=12)
        assert go is True

    def test_stops_when_all_tasks_done(self):
        go, reason = L.should_continue(enabled=True, open_tasks=0, iterations=1, max_iterations=12)
        assert go is False
        assert "done" in reason.lower()

    def test_stops_at_the_cap_even_with_open_tasks(self):
        # The runaway guard: never loop forever, no matter what the model claims.
        go, reason = L.should_continue(enabled=True, open_tasks=5, iterations=12, max_iterations=12)
        assert go is False
        assert "cap" in reason.lower() or "limit" in reason.lower()

    def test_stops_when_disabled(self):
        go, _ = L.should_continue(enabled=False, open_tasks=5, iterations=0, max_iterations=12)
        assert go is False

    def test_one_below_the_cap_still_continues(self):
        go, _ = L.should_continue(enabled=True, open_tasks=1, iterations=11, max_iterations=12)
        assert go is True


class TestPrompts:
    def test_seed_prompt_carries_the_user_task_and_nudges_task_list(self):
        seed = L.seed_prompt("add a CLI flag")
        assert "add a CLI flag" in seed
        assert "TaskCreate" in seed              # nudge the model to make a task list

    def test_continue_prompt_pushes_to_finish(self):
        assert "continue" in L.CONTINUE_PROMPT.lower()


class TestMaxIterations:
    def test_default_cap_is_sane_and_positive(self):
        assert 1 <= L.default_max_iterations() <= 100

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KORGEX_LOOP_MAX", "5")
        assert L.default_max_iterations() == 5

    def test_bad_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("KORGEX_LOOP_MAX", "not-a-number")
        assert L.default_max_iterations() == L._DEFAULT_MAX


# ── the REPL driver actually grinds + stops ──────────────────────────────────

class _LoopHarness(Repl):
    """A Repl with config/agent/turn stubbed out, so we exercise ONLY _run_loop's
    drive logic: how many continue-turns it issues and when it stops."""

    def __init__(self, open_count):
        self.out = io.StringIO()
        self._agent = None
        self._turn = 0
        self._rewind = None
        self._open_count = open_count          # callable: (turns_done) -> remaining
        self.calls = []

    def _open_task_count(self):
        return self._open_count(len(self.calls))

    def _run_turn(self, text):
        self.calls.append(text)


def test_run_loop_seeds_then_grinds_until_tasks_finish():
    # tasks drop to 0 after two turns total (seed + one continue)
    h = _LoopHarness(open_count=lambda turns: max(0, 2 - turns))
    h._run_loop("do the thing")
    assert "do the thing" in h.calls[0]          # seed turn nudges a plan
    assert h.calls[1] == L.CONTINUE_PROMPT        # then one grind turn
    assert len(h.calls) == 2
    assert "done" in h.out.getvalue().lower()


def test_run_loop_stops_at_the_cap_when_tasks_never_clear(monkeypatch):
    monkeypatch.setenv("KORGEX_LOOP_MAX", "3")
    h = _LoopHarness(open_count=lambda turns: 5)   # always work left → must be capped
    h._run_loop(None)                               # resume-mode grind on existing tasks
    assert len(h.calls) == 3                        # exactly the cap, no more
    assert all(c == L.CONTINUE_PROMPT for c in h.calls)
    assert "cap" in h.out.getvalue().lower()


def test_run_loop_no_arg_no_tasks_prints_usage():
    h = _LoopHarness(open_count=lambda turns: 0)
    h._run_loop(None)
    assert h.calls == []                            # nothing to grind
    assert "usage" in h.out.getvalue().lower()
