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
