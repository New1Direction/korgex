"""Forgiving SEARCH/REPLACE application — tolerate whitespace/indent drift so an
edit doesn't fail just because the model's old_string is off by some spaces.

Deliberately conservative: exact match first, then a whitespace-tolerant
line-block match. NO similarity guessing (that risks editing the wrong code).
"""
from src.fuzzy_patch import find_and_replace


def test_exact_match():
    out, status, _ = find_and_replace("a\nb\nc\n", "b", "B")
    assert out == "a\nB\nc\n" and status == "exact"


def test_whitespace_indent_drift_matches():
    content = "def f():\n        return 1\n"          # 8-space indent in the file
    search = "def f():\n    return 1"                  # model used 4 spaces
    out, status, _ = find_and_replace(content, search, "def f():\n        return 2")
    assert status == "fuzzy-whitespace"
    assert "return 2" in out and "return 1" not in out


def test_trailing_whitespace_drift_matches():
    content = "x = 1   \ny = 2\n"                      # trailing spaces in the file
    search = "x = 1\ny = 2"
    out, status, _ = find_and_replace(content, search, "x = 10\ny = 2")
    assert status == "fuzzy-whitespace" and "x = 10" in out


def test_not_found_leaves_content_unchanged():
    out, status, _ = find_and_replace("a\nb\n", "zzz", "Q")
    assert status == "not-found" and out == "a\nb\n"


def test_different_content_does_not_fuzzy_match():
    # only whitespace differs is OK; DIFFERENT code must NOT be silently replaced
    out, status, _ = find_and_replace("total = a + b\n", "total = a - b", "X")
    assert status == "not-found" and out == "total = a + b\n"


def test_exact_preferred_and_replaces_first_only():
    out, status, _ = find_and_replace("x\nx\n", "x", "Y")
    assert out == "Y\nx\n" and status == "exact"
