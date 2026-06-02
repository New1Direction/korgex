"""Tests for write-path text safety (src/text_safety.py).

Model output occasionally carries stray C0 control characters — e.g. a mangled
em-dash that arrived as \\x1a\\x14 (seen for real in a humanize_error edit). Writing
those into a user's source file is silent corruption. strip_control_chars removes
C0/DEL control chars (keeping tab/newline/CR) from any text korgex is about to
write, so corruption from ANY upstream source (model glitch, split UTF-8 in the
stream, bad decode) can never reach disk.
"""
from src.text_safety import strip_control_chars


def test_strips_the_real_corruption_bytes():
    cleaned, removed = strip_control_chars("unavailable \x1a\x14 retry")
    assert cleaned == "unavailable  retry"
    assert removed == 2


def test_preserves_unicode_like_em_dash():
    cleaned, removed = strip_control_chars("ok — fine · café")
    assert cleaned == "ok — fine · café"   # >= 0x20 chars are never touched
    assert removed == 0


def test_keeps_tab_newline_cr():
    text = "line1\n\tindented\r\nline2"
    cleaned, removed = strip_control_chars(text)
    assert cleaned == text
    assert removed == 0


def test_strips_other_c0_controls_and_del():
    cleaned, removed = strip_control_chars("x\x00\x07\x1f\x7fy")
    assert cleaned == "xy"
    assert removed == 4


def test_empty_and_clean_are_noops():
    assert strip_control_chars("") == ("", 0)
    assert strip_control_chars("perfectly normal code()") == ("perfectly normal code()", 0)
