"""Startup banner — a designed boot screen (wordmark + status line), not one bare line.

The REPL opened with a single plain line. Real agent CLIs open with a wordmark +
a status line (model, cwd, version) + a hint. These pin the pure assembly; the
rich rendering is a thin shell over it.
"""
from src import banner as B


def test_wordmark_is_multiline_ascii():
    art = B.wordmark()
    assert isinstance(art, str)
    assert art.count("\n") >= 2  # a real multi-row wordmark, not one line


def test_status_line_shows_model_and_version():
    line = B.status_line(model="claude-sonnet-4-6", cwd="/Users/x/proj", version="0.10.0")
    assert "claude-sonnet-4-6" in line
    assert "0.10.0" in line


def test_status_line_shortens_home_to_tilde():
    import os
    home = os.path.expanduser("~")
    line = B.status_line(model="m", cwd=os.path.join(home, "proj"), version="1")
    assert "~/proj" in line and home not in line


def test_hint_line_lists_core_commands():
    h = B.hint_line()
    assert "/help" in h and "/exit" in h


def test_startup_text_assembles_all_parts():
    text = B.startup_text(model="gpt-4o", cwd="/tmp/p", version="9.9.9",
                          configured=True)
    assert "gpt-4o" in text and "9.9.9" in text
    # the wordmark's letters/box-chars are present (designed, not a bare line)
    assert text.count("\n") >= 4


def test_startup_text_unconfigured_prompts_setup():
    text = B.startup_text(model="m", cwd="/tmp", version="1", configured=False)
    assert "setup" in text.lower()  # nudges `korgex setup` when no provider yet
