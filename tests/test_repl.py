"""korgex REPL input parser — the pure core of the conversational shell.

A line is either a slash-command or a turn (a message to the agent). The parser
is pure + total so the streaming/IO loop around it stays a thin shell.
"""
from src import repl as R


def test_plain_text_is_a_turn():
    cmd = R.parse_repl_input("add rate limiting to the api")
    assert cmd.kind == "turn"
    assert cmd.arg == "add rate limiting to the api"


def test_slash_exit_variants():
    for line in ("/exit", "/quit", "/q"):
        assert R.parse_repl_input(line).kind == "exit"


def test_slash_help():
    assert R.parse_repl_input("/help").kind == "help"
    assert R.parse_repl_input("/?").kind == "help"


def test_slash_clear():
    assert R.parse_repl_input("/clear").kind == "clear"


def test_model_command_without_arg_lists():
    cmd = R.parse_repl_input("/model")
    assert cmd.kind == "model"
    assert cmd.arg is None


def test_model_command_with_arg_switches():
    cmd = R.parse_repl_input("/model claude-opus-4-8")
    assert cmd.kind == "model"
    assert cmd.arg == "claude-opus-4-8"


def test_unknown_slash_is_an_error_not_a_turn():
    cmd = R.parse_repl_input("/bogus")
    assert cmd.kind == "unknown"
    assert "bogus" in cmd.arg


def test_blank_line_is_noop():
    assert R.parse_repl_input("   ").kind == "noop"


def test_leading_slash_in_a_sentence_is_still_a_turn_only_if_spaced():
    # A real slash command is the first token; "/foo" alone is a command attempt,
    # but text that merely contains a slash mid-line is a turn.
    assert R.parse_repl_input("what does /etc/hosts do").kind == "turn"


def test_slash_skills_parses():
    assert R.parse_repl_input("/skills").kind == "skills"
    assert R.parse_repl_input("/skills").arg is None


def test_slash_skills_curate_carries_the_arg():
    # `/skills curate` routes to the consolidation pass, not the listing.
    cmd = R.parse_repl_input("/skills curate")
    assert cmd.kind == "skills"
    assert cmd.arg == "curate"


def test_slash_tasks_parses():
    assert R.parse_repl_input("/tasks").kind == "tasks"


def test_slash_jobs_parses():
    assert R.parse_repl_input("/jobs").kind == "jobs"


def test_slash_rewind_parses():
    assert R.parse_repl_input("/rewind").kind == "rewind"
    assert R.parse_repl_input("/rewind").arg is None
    assert R.parse_repl_input("/rewind 2").arg == "2"


def test_slash_version_parses():
    assert R.parse_repl_input("/version").kind == "version"
    assert R.parse_repl_input("/version").arg is None


def test_suggest_command_finds_close_typos():
    assert R.suggest_command("skils") == "skills"
    assert R.suggest_command("hlep") == "help"
    assert R.suggest_command("retwind") == "rewind"


def test_suggest_command_returns_none_for_nonsense():
    assert R.suggest_command("xyzzy") is None
    assert R.suggest_command("") is None


def test_known_commands_covers_the_real_set():
    # the suggester's vocabulary must include the actual commands
    for c in ("model", "plan", "skills", "diff", "loop", "rewind", "help", "exit"):
        assert c in R.KNOWN_COMMANDS


def test_slash_diff_parses():
    assert R.parse_repl_input("/diff").kind == "diff"
    assert R.parse_repl_input("/diff").arg is None
    assert R.parse_repl_input("/diff 3").arg == "3"


def test_slash_trace_parses():
    assert R.parse_repl_input("/trace").kind == "trace"
    assert R.parse_repl_input("/trace all").arg == "all"


def test_slash_explain_parses():
    assert R.parse_repl_input("/explain").kind == "explain"
    assert R.parse_repl_input("/explain on").arg == "on"
    assert R.parse_repl_input("/explain off").arg == "off"


def test_slash_loop_parses_with_and_without_task():
    cmd = R.parse_repl_input("/loop build the parser")
    assert cmd.kind == "loop"
    assert cmd.arg == "build the parser"
    assert R.parse_repl_input("/loop").arg is None


def test_bang_runs_a_shell_command_not_an_agent_turn():
    assert R.parse_repl_input("!ls -la").kind == "shell"
    assert R.parse_repl_input("!ls -la").arg == "ls -la"
    assert R.parse_repl_input("  !git status  ").arg == "git status"


def test_bare_bang_is_shell_with_empty_arg():
    assert R.parse_repl_input("!").kind == "shell"
    assert R.parse_repl_input("!").arg == ""


def test_bang_only_at_start_is_a_command():
    # An exclamation mid-text is a normal message, not a shell escape.
    assert R.parse_repl_input("ship it!").kind == "turn"
    assert R.parse_repl_input("does it work!?").kind == "turn"


def test_run_shell_executes_and_prints_output():
    import io

    class _ShellHarness(R.Repl):
        def __init__(self, cwd):
            self.out = io.StringIO()
            self.repo_root = cwd

    h = _ShellHarness(cwd=".")
    h._run_shell("echo korgex_shell_ok")
    assert "korgex_shell_ok" in h.out.getvalue()


def test_repl_has_repo_root_set_to_cwd():
    # Regression: self.repo_root is referenced by /skills, @-mentions, skill
    # learning and the curator. It was never assigned — so those paths hit an
    # AttributeError (silently, where wrapped), and skills never actually learned.
    import io
    import os as _os

    r = R.Repl(out=io.StringIO())
    assert r.repo_root == _os.getcwd()


def test_mcp_configured_reflects_config_file(tmp_path, monkeypatch):
    import io
    import json

    import src.mcp_config as MC
    monkeypatch.chdir(tmp_path)
    # Isolate from the user's real global config by scoping sources to this dir.
    monkeypatch.setattr(MC, "default_sources", lambda cwd=None: [str(tmp_path / "mcp.json")])
    r = R.Repl(out=io.StringIO())
    assert r._mcp_configured() is False
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"x": {"url": "https://y"}}}))
    assert r._mcp_configured() is True


def test_mcp_names_lists_all_configured_servers(tmp_path, monkeypatch):
    import io
    import json

    import src.mcp_config as MC
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(MC, "default_sources", lambda cwd=None: [str(tmp_path / "mcp.json")])
    (tmp_path / "mcp.json").write_text(json.dumps(
        {"mcpServers": {"alpha": {"url": "https://a"}, "beta": {"command": "x"}}}))
    r = R.Repl(out=io.StringIO())
    assert sorted(r._mcp_names()) == ["alpha", "beta"]


def test_slash_plan_parses():
    assert R.parse_repl_input("/plan").kind == "plan"
    assert R.parse_repl_input("/plan").arg is None
    assert R.parse_repl_input("/plan on").kind == "plan"
    assert R.parse_repl_input("/plan on").arg == "on"
    assert R.parse_repl_input("/plan approve").arg == "approve"


# ── the loop: reads lines from an injectable reader, dispatches via handle ──────

def _repl(monkeypatch):
    """A Repl whose model resolves without touching real config."""
    from src import repl as RM
    from src import config as C
    cfg = C.Config(default_model="claude-sonnet-4-6", providers=[])
    return RM.Repl(cfg=cfg)


def test_loop_dispatches_lines_until_exit(monkeypatch):
    r = _repl(monkeypatch)
    seen = []
    r.handle = lambda cmd: (seen.append(cmd.kind), cmd.kind != "exit")[1]
    # a reader that yields two turns then an exit command
    lines = iter(["hello", "/help", "/exit"])
    r._read_line = lambda: next(lines)
    r._banner = lambda: None  # skip the rich banner in the loop test
    r._run_simple()
    assert seen == ["turn", "help", "exit"]


def test_loop_stops_on_eof(monkeypatch):
    r = _repl(monkeypatch)
    seen = []
    r.handle = lambda cmd: seen.append(cmd.kind) or True
    def eof():
        raise EOFError
    r._read_line = eof
    r._banner = lambda: None
    r._run_simple()  # must return cleanly, not hang or raise
    assert seen == []


def test_read_line_uses_the_prompt_session(monkeypatch):
    """The real reader delegates to a prompt_toolkit session (bottom-anchored)."""
    r = _repl(monkeypatch)
    calls = {}
    class FakeSession:
        def prompt(self, *a, **k):
            calls["prompted"] = True
            calls["kwargs"] = k
            return "typed text"
    r._session_obj = FakeSession()
    out = r._read_line()
    assert out == "typed text"
    assert calls.get("prompted")
    # bottom_toolbar wired → the status line lives at the bottom of the window
    assert "bottom_toolbar" in calls["kwargs"]
