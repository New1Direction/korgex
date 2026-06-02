"""Tests for @-file mentions (src/mentions.py).

A daily-driver ergonomic that CC/Cursor have and korgex lacked: typing
``@path/to/file`` in a prompt pulls that file's contents into the turn, so you can
say "refactor @src/auth.py to use @src/db.py" without pasting. The expander is pure
(text + cwd + a read hook → expanded text + which files attached), so it tests
without touching the real filesystem, and it's conservative: only real files are
inlined; a bare @handle or an email is left untouched.
"""
import os

from src import mentions as M


# ── finding mentions in text ─────────────────────────────────────────────────

class TestFindMentions:
    def test_finds_paths_after_at(self):
        assert M.find_mentions("look at @src/a.py and @b.txt") == ["src/a.py", "b.txt"]

    def test_strips_trailing_punctuation(self):
        assert M.find_mentions("see @a.py, then @b.py.") == ["a.py", "b.py"]

    def test_ignores_at_inside_a_word_like_an_email(self):
        assert M.find_mentions("email me@example.com about it") == []

    def test_dedupes_preserving_order(self):
        assert M.find_mentions("@a.py @b.py @a.py") == ["a.py", "b.py"]

    def test_no_mentions(self):
        assert M.find_mentions("just a normal sentence") == []


# ── expanding mentions into the prompt ───────────────────────────────────────

class TestExpandMentions:
    def test_inlines_an_existing_file(self, tmp_path):
        (tmp_path / "a.py").write_text("print('hi')\n")
        res = M.expand_mentions("explain @a.py", cwd=str(tmp_path))
        assert res["attached"] == ["a.py"]
        assert "explain @a.py" in res["text"]      # original instruction kept
        assert "print('hi')" in res["text"]         # contents inlined
        assert "a.py" in res["text"]

    def test_skips_a_missing_file(self, tmp_path):
        res = M.expand_mentions("see @nope.py", cwd=str(tmp_path))
        assert res["attached"] == []
        assert res["text"] == "see @nope.py"        # unchanged

    def test_reports_a_missing_path_like_mention(self, tmp_path):
        # A path-looking @mention that doesn't resolve is surfaced (likely a typo),
        # not silently dropped.
        res = M.expand_mentions("edit @src/nope.py please", cwd=str(tmp_path))
        assert res["attached"] == []
        assert res["missed"] == ["src/nope.py"]

    def test_bare_word_mention_is_not_a_miss(self, tmp_path):
        # "@bob" has no path shape — it's not a file reference, so no false warning.
        res = M.expand_mentions("ping @bob about it", cwd=str(tmp_path))
        assert res["missed"] == []

    def test_no_mentions_returns_text_unchanged(self, tmp_path):
        res = M.expand_mentions("hello there", cwd=str(tmp_path))
        assert res["text"] == "hello there"
        assert res["attached"] == []

    def test_caps_large_files(self, tmp_path):
        (tmp_path / "big.txt").write_text("x" * 5000)
        res = M.expand_mentions("@big.txt", cwd=str(tmp_path), max_bytes=1000)
        assert res["attached"] == ["big.txt"]
        # only the cap's worth of content is inlined, plus a truncation marker
        assert res["text"].count("x") <= 1100
        assert "truncated" in res["text"].lower()

    def test_multiple_files_each_inlined(self, tmp_path):
        (tmp_path / "a.py").write_text("AAA")
        (tmp_path / "b.py").write_text("BBB")
        res = M.expand_mentions("merge @a.py and @b.py", cwd=str(tmp_path))
        assert res["attached"] == ["a.py", "b.py"]
        assert "AAA" in res["text"] and "BBB" in res["text"]

    def test_a_directory_mention_is_not_inlined(self, tmp_path):
        os.makedirs(str(tmp_path / "src"))
        res = M.expand_mentions("look in @src", cwd=str(tmp_path))
        assert res["attached"] == []                # only files, not dirs
