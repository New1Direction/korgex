"""Bottom-pinned inline TUI: a non-full-screen prompt_toolkit
Application with the input TextArea pinned to the bottom, output scrolling above
in preserved scrollback. These pin the pure pieces (prompt text, bottom-push math,
accept routing, status line); the live paint is verified by eye.
"""
from src import tui_app as T


# ── bottom-push math: scroll the cursor to the last row at startup ─────────────

def test_bottom_push_lines_fills_to_last_row():
    # On a 30-row terminal, push 29 newlines so content starts at the bottom.
    assert T.bottom_push_count(30) == 29


def test_bottom_push_noop_on_tiny_terminal():
    assert T.bottom_push_count(2) == 0
    assert T.bottom_push_count(1) == 0


# ── prompt text ────────────────────────────────────────────────────────────────

def test_prompt_text_is_the_accent_caret():
    assert "›" in T.prompt_text()


# ── status / toolbar line ──────────────────────────────────────────────────────

def test_status_text_shows_model_and_hints():
    s = T.status_text(model="claude-sonnet-4-6", plan=False)
    assert "claude-sonnet-4-6" in s
    assert "/exit" in s


def test_status_text_shows_plan_badge_when_active():
    s = T.status_text(model="m", plan=True)
    assert "plan" in s.lower()


# ── accept routing: a submitted line is dispatched, blanks ignored ─────────────

def test_on_accept_dispatches_nonblank():
    seen = []
    T.handle_submission("hello world", dispatch=seen.append)
    assert seen == ["hello world"]


def test_on_accept_ignores_blank():
    seen = []
    T.handle_submission("   ", dispatch=seen.append)
    assert seen == []  # whitespace-only submissions don't fire a turn
