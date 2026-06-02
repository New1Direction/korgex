"""Regression: the Edit handler reports what it actually did.

The core merge-diff Edit handler had ZERO handler-level test coverage — which is how
a bad change (build the real result, then `return {"result": "PR operation
executed"}`, discarding filepath/changes) sailed past ruff AND the full suite
unnoticed during a dogfood run. This pins the contract so that can't recur.
"""
import os

from src.tools_impl import tool_replace_with_git_merge_diff, tool_write_file


def test_edit_returns_the_real_result_not_a_hardcoded_string(tmp_path):
    d = str(tmp_path)
    tool_write_file("a.py", "x = 1\ny = 2\n", context={"repo_root": d})
    diff = "<<<<<<< SEARCH\ny = 2\n=======\ny = 3\n>>>>>>> REPLACE"
    res = tool_replace_with_git_merge_diff("a.py", diff, context={"repo_root": d})
    assert "error" not in res
    assert res["filepath"] == "a.py"                       # the REAL file, not a constant
    assert "Applied" in res["result"] and "change" in res["result"]
    assert open(os.path.join(d, "a.py")).read() == "x = 1\ny = 3\n"   # edit landed


def test_edit_with_unfindable_search_errors_clearly(tmp_path):
    d = str(tmp_path)
    tool_write_file("a.py", "x = 1\n", context={"repo_root": d})
    diff = "<<<<<<< SEARCH\nNOT IN FILE\n=======\nz = 9\n>>>>>>> REPLACE"
    res = tool_replace_with_git_merge_diff("a.py", diff, context={"repo_root": d})
    assert "error" in res
    assert "x = 1\n" == open(os.path.join(d, "a.py")).read()          # file untouched
