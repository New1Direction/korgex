"""Tests for inline diff rendering (src/diff_view.py).

So you SEE what the agent changed — a colored unified diff per file, the CC/Cursor
feel. Pure (before/after strings in, a rendered diff string out): ANSI color is
optional so tests can assert on plain content, hunks are capped so a giant edit
doesn't flood the terminal, and an unchanged file renders nothing.
"""
from src import diff_view as DV


class TestRenderUnifiedDiff:
    def test_shows_added_and_removed_lines(self):
        out = DV.render_unified_diff("a.py", "x = 1\ny = 2\n", "x = 1\ny = 3\n", color=False)
        assert "-y = 2" in out
        assert "+y = 3" in out
        assert "a.py" in out               # the path heads the diff

    def test_unchanged_renders_nothing(self):
        assert DV.render_unified_diff("a.py", "same\n", "same\n", color=False) == ""

    def test_created_file_is_all_additions(self):
        out = DV.render_unified_diff("new.py", "", "hello\nworld\n", color=False)
        assert "+hello" in out and "+world" in out

    def test_caps_long_diffs_with_a_truncation_note(self):
        before = ""
        after = "\n".join(f"line {i}" for i in range(500)) + "\n"
        out = DV.render_unified_diff("big.py", before, after, color=False, max_lines=20)
        assert out.count("\n") <= 25                 # capped, not 500 lines
        assert "truncat" in out.lower()

    def test_color_wraps_added_removed_lines(self):
        out = DV.render_unified_diff("a.py", "old\n", "new\n", color=True)
        # ANSI escape present when colored (robust for code containing brackets —
        # we use ANSI, not rich markup, on purpose)
        assert "\033[" in out

    def test_no_color_has_no_escape_codes(self):
        out = DV.render_unified_diff("a.py", "old\n", "new\n", color=False)
        assert "\033[" not in out


class TestRenderTurnDiffs:
    def test_renders_each_changed_file(self):
        records = [("a.py", "1\n"), ("b.py", None)]      # b.py was created
        post = {"a.py": "2\n", "b.py": "new\n"}
        out = DV.render_turn_diffs(records, read_fn=lambda p: post[p], color=False)
        assert "a.py" in out and "b.py" in out
        assert "-1" in out and "+2" in out and "+new" in out

    def test_empty_when_nothing_changed(self):
        out = DV.render_turn_diffs([("a.py", "same\n")], read_fn=lambda p: "same\n", color=False)
        assert out == ""
