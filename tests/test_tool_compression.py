"""Tests for src/tool_compression.py — structure-aware compact views.

Pure, stdlib-only, offline. Compact views only shrink the MODEL'S view; the
full original is sealed elsewhere (the ledger blob store) and recoverable via
Retrieve. These tests assert the views are (a) much shorter than the raw value,
(b) still informative (names + sizes), and (c) NEVER raise on weird input.
"""
from __future__ import annotations

import json

from src import tool_compression as tc


def test_detect_kind_json_python_text():
    assert tc.detect_kind({"a": 1}) == "json"
    assert tc.detect_kind([1, 2, 3]) == "json"
    assert tc.detect_kind('{"a": 1, "b": [1,2,3]}') == "json"
    assert tc.detect_kind("def f():\n    return 1\n") == "python"
    assert tc.detect_kind("plain\nlog\nlines\n") == "text"


def test_compact_json_shrinks_but_names_keys_and_sizes():
    obj = {
        "items": [{"id": i, "name": "x" * 50} for i in range(200)],
        "meta": {"total": 200, "note": "y" * 500},
        "flag": True,
    }
    raw = json.dumps(obj)
    view = tc.compact_json(obj)
    assert isinstance(view, str)
    assert len(view) < len(raw) // 4          # MUCH shorter
    assert "items" in view and "meta" in view and "flag" in view  # names top-level keys
    assert "200" in view                       # reports the container length


def test_compact_python_skeleton_keeps_signatures_drops_bodies():
    src = (
        "import os\n"
        "\n"
        "MAGIC = 42\n"
        "\n"
        "class Widget:\n"
        '    """A widget.\n\n    Multi-line docstring.\n    """\n'
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "        self.secret_internal_value = 99999\n"
        "\n"
        "    def render(self, mode='fast'):\n"
        "        return self.name * 1000\n"
        "\n"
        "def top_level(a, b, c=3):\n"
        '    """Docline."""\n'
        "    body_only_token = a + b + c\n"
        "    return body_only_token\n"
    ) + "\n# filler\n" * 40
    view = tc.compact_python(src)
    assert "class Widget" in view
    assert "def render" in view
    assert "def top_level" in view
    # Bodies are dropped:
    assert "secret_internal_value" not in view
    assert "body_only_token" not in view
    # Docstring first line surfaces, but not the whole multi-line body:
    assert "A widget." in view
    assert "Multi-line docstring." not in view
    assert len(view) < len(src)


def test_compact_python_malformed_falls_back_to_text_no_raise():
    bad = "def f(:\n  this is not python ::: <<<\n" + "line\n" * 100
    view = tc.compact_python(bad)          # must not raise
    assert isinstance(view, str)
    assert len(view) < len(bad)


def test_compact_text_head_tail_and_counts():
    lines = [f"line-{i}" for i in range(1000)]
    s = "\n".join(lines)
    view = tc.compact_text(s)
    assert "line-0" in view                 # head
    assert "line-999" in view               # tail
    assert "1000" in view                   # total line count reported
    assert len(view) < len(s) // 4


def test_compact_view_dispatches_and_never_raises():
    # dict -> json view
    assert "k" in tc.compact_view({"k": list(range(100))})
    # python source -> python view
    assert "def f" in tc.compact_view("def f(x):\n    return x\n")
    # plain text -> text view
    assert isinstance(tc.compact_view("a\n" * 200), str)
    # Weird / hostile inputs must degrade to a str, never raise:
    for weird in (None, b"\x00\x01\x02bytes", 12345, 3.14, {"deep": {"deep": {"deep": [1] * 50}}}):
        out = tc.compact_view(weird)
        assert isinstance(out, str)


def test_compressors_registry_is_keyed_by_kind_and_injectable():
    assert set(tc.COMPRESSORS.keys()) == {"json", "python", "text"}
    # Injectable for tests (web_tools-style): swap a compressor and see it used.
    sentinel = "SWAPPED-COMPRESSOR-OUTPUT"
    orig = tc.COMPRESSORS["text"]
    try:
        tc.COMPRESSORS["text"] = lambda s: sentinel
        assert tc.compact_view("just\nsome\ntext\n") == sentinel
    finally:
        tc.COMPRESSORS["text"] = orig
