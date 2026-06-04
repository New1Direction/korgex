"""Single-message repetition rail — catch a model spewing the same line/block within ONE
response (a stuck loop, not a tool call). Complements RepeatGuard (repeated *tool calls*
across turns). Pure + LLM-free, like the rest of loop_guard, using a P×K score
(pattern_length x repetitions) so short patterns need more reps, long ones fewer.
"""
from src import loop_guard as LG


def test_detect_repetition_flags_a_spammed_line():
    text = "Working on it.\n" + ("calling the tool again now\n" * 12)
    rep = LG.detect_repetition(text)
    assert rep and rep["kind"] == "single_line"
    assert rep["reps"] >= 12


def test_detect_repetition_flags_a_repeated_block():
    block = "step 1: analyze the failure\nstep 2: edit the file\nstep 3: run the tests\n"
    rep = LG.detect_repetition("Here's my plan:\n" + block * 5)
    assert rep and rep["kind"] == "multi_line"
    assert rep["reps"] >= 4


def test_detect_repetition_ignores_normal_varied_text():
    text = ("I read auth.py, found the null check bug, fixed it, ran the tests, "
            "and they all pass now. Here's a short summary of the change.")
    assert LG.detect_repetition(text) is None


def test_detect_repetition_below_threshold_is_none():
    assert LG.detect_repetition("log line here\n" * 3) is None      # 3 reps < floor


def test_detect_repetition_ignores_blank_line_runs():
    assert LG.detect_repetition("done\n\n\n\n\n\n\nthanks") is None   # blank runs aren't a loop


def test_repetition_guard_caps_its_nudges():
    g = LG.RepetitionGuard(max_nudges=2)
    rep = {"kind": "single_line", "reps": 20, "pattern": "x"}
    assert g.nudge(rep) is not None
    assert g.nudge(rep) is not None
    assert g.nudge(rep) is None                                      # capped — the nudge can't loop
