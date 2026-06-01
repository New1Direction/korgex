"""Output routing for the bottom-anchored REPL.

Under prompt_toolkit's patch_stdout, raw ANSI written via plain print()/stdout is
swallowed by its StdoutProxy — which garbled korgex's spinner + streamed text.
The fix: route output through prompt_toolkit's ANSI renderer. These pin the pure
routing helper; the actual terminal paint is verified by eye.
"""
from src import pt_output as PO


def test_emit_falls_back_to_plain_print_without_ptk(monkeypatch, capsys):
    # Force the no-prompt_toolkit path → plain print (so non-interactive/pipes work)
    monkeypatch.setattr(PO, "_ptk_available", lambda: False)
    PO.emit("hello world")
    assert "hello world" in capsys.readouterr().out


def test_emit_routes_through_ptk_when_available(monkeypatch):
    # When prompt_toolkit is present, emit goes through its ANSI printer, not print()
    calls = {}
    monkeypatch.setattr(PO, "_ptk_available", lambda: True)
    monkeypatch.setattr(PO, "_ptk_print_ansi", lambda text: calls.setdefault("text", text))
    PO.emit("\033[2mdim text\033[0m")
    assert "dim text" in calls["text"]


def test_emit_never_raises_on_bad_console(monkeypatch):
    # A renderer that blows up must not crash the turn — emit swallows + falls back.
    monkeypatch.setattr(PO, "_ptk_available", lambda: True)
    def boom(text): raise RuntimeError("no console")
    monkeypatch.setattr(PO, "_ptk_print_ansi", boom)
    PO.emit("x")  # must not raise


def test_render_rich_returns_ansi_string():
    # rich markup → an ANSI string we can route through emit (color preserved).
    s = PO.render_rich("[bold]hi[/bold]")
    assert "hi" in s
    assert "\033[" in s  # contains ANSI escapes (color/bold), i.e. it rendered
