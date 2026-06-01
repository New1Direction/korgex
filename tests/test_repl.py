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
