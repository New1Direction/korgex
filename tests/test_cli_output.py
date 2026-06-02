"""Regression: `korgex "task"` must print its answer.

Found by dogfooding: `korgex "..."` exited 0 but printed NOTHING. The shim only
emitted the final text under --quiet, assuming non-quiet runs streamed it live —
but the naked-prompt path never sets interactive truthy, so it never streamed and
never printed. _should_emit_final fixes the rule: print the result whenever it
wasn't already streamed live.
"""
from src.cli import _should_emit_final


def test_emits_when_not_interactive():
    # naked-prompt / script / quiet → not streamed, so print it
    assert _should_emit_final("the answer", interactive=False) is True
    assert _should_emit_final("the answer", interactive=None) is True


def test_skips_when_streamed_live():
    # interactive TTY already streamed the text — don't double-print
    assert _should_emit_final("the answer", interactive=True) is False


def test_empty_text_is_never_emitted():
    assert _should_emit_final("", interactive=False) is False
    assert _should_emit_final(None, interactive=False) is False
