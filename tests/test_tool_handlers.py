"""Coverage for the core tool handlers (src/tools_impl.py).

The most-used handlers — Read, list_files, delete, Bash — had ZERO test coverage,
which is how a regression in the Edit handler slipped past ruff + the full suite
during a dogfood run. These pin the primitives the agent leans on every turn:
the happy path and the error path.
"""
from src.tools_impl import (
    tool_delete_file,
    tool_list_files,
    tool_read_file,
    tool_run_in_bash_session,
    tool_write_file,
)


def _ctx(d):
    return {"repo_root": str(d)}


class TestReadFile:
    def test_reads_content(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello\nworld\n")
        res = tool_read_file("a.txt", context=_ctx(tmp_path))
        assert res["content"] == "hello\nworld\n"
        assert res["filepath"] == "a.txt"
        assert res["size"] == 12

    def test_missing_file_errors(self, tmp_path):
        res = tool_read_file("nope.txt", context=_ctx(tmp_path))
        assert "error" in res and "does not exist" in res["error"]


class TestWriteFile:
    def test_writes_and_creates_parent_dirs(self, tmp_path):
        res = tool_write_file("sub/dir/a.txt", "data", context=_ctx(tmp_path))
        assert "error" not in res
        assert (tmp_path / "sub" / "dir" / "a.txt").read_text() == "data"


class TestListFiles:
    def test_lists_directory(self, tmp_path):
        (tmp_path / "one.py").write_text("")
        (tmp_path / "two.py").write_text("")
        res = tool_list_files(context=_ctx(tmp_path))
        assert "error" not in res
        joined = "\n".join(res["files"])
        assert "one.py" in joined and "two.py" in joined   # would fail if ls errored

    def test_missing_dir_errors(self, tmp_path):
        res = tool_list_files("nope", context=_ctx(tmp_path))
        assert "error" in res


class TestDeleteFile:
    def test_deletes_existing(self, tmp_path):
        (tmp_path / "x.txt").write_text("bye")
        res = tool_delete_file("x.txt", context=_ctx(tmp_path))
        assert "error" not in res
        assert not (tmp_path / "x.txt").exists()

    def test_missing_file_errors(self, tmp_path):
        res = tool_delete_file("ghost.txt", context=_ctx(tmp_path))
        assert "error" in res


class TestBashSession:
    def test_runs_a_foreground_command(self, tmp_path):
        res = tool_run_in_bash_session("echo korgex_bash_ok", context=_ctx(tmp_path))
        assert "korgex_bash_ok" in str(res)
