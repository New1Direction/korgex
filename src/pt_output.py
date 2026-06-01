"""Output routing for the bottom-anchored REPL.

Under prompt_toolkit's ``patch_stdout``, raw ANSI written via plain ``print`` /
``sys.stdout.write`` is swallowed by its ``StdoutProxy`` — which garbled korgex's
spinner (raw ``\\r``/escapes) and streamed tokens. The fix is to route output
through prompt_toolkit's own ANSI renderer (``print_formatted_text(ANSI(...))``),
which parses the escapes and paints them cleanly above the pinned input.

``emit`` is the single sink everything in interactive mode should print through.
``render_rich`` turns rich markup into an ANSI string so existing rich output can
flow through the same sink. Both degrade to plain ``print`` when prompt_toolkit
isn't available (pipes / non-interactive), so nothing breaks headless.
"""
from __future__ import annotations


def _ptk_available() -> bool:
    try:
        import prompt_toolkit  # noqa: F401
        return True
    except Exception:
        return False


def _ptk_print_ansi(text: str) -> None:
    """Print one chunk through prompt_toolkit's ANSI parser (cooperates with
    patch_stdout; renders real color instead of being swallowed)."""
    from prompt_toolkit import print_formatted_text
    from prompt_toolkit.formatted_text import ANSI
    print_formatted_text(ANSI(text), end="")


def emit(text: str) -> None:
    """The single interactive output sink for CONTENT (streamed tokens, blocks,
    tool lines). Routes through prompt_toolkit's ANSI renderer when available so it
    cooperates with the bottom-anchored input; else plain print. Never raises."""
    if _ptk_available():
        try:
            _ptk_print_ansi(text)
            return
        except Exception:
            pass
    try:
        print(text, end="")
    except Exception:
        pass


def emit_raw(text: str) -> None:
    """Write cursor-control sequences (\\r, ESC[2K) straight to the real terminal.

    These MUST bypass prompt_toolkit — its print_formatted_text strips carriage
    returns, so an in-place spinner routed through it would append every frame
    instead of overwriting one line. Used only for the transient spinner, which
    clears itself before any real content is emitted."""
    import sys
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass


def render_rich(markup: str, width: int | None = None) -> str:
    """Render rich markup to an ANSI string (truecolor, no soft-wrap mangling), so
    rich panels/markup can be routed through ``emit``. Falls back to the raw markup
    text if rich isn't importable."""
    try:
        import shutil
        from io import StringIO
        from rich.console import Console
        buf = StringIO()
        w = width or shutil.get_terminal_size((80, 24)).columns
        Console(file=buf, force_terminal=True, color_system="truecolor",
                highlight=False, width=w).print(markup)
        return buf.getvalue()
    except Exception:
        return markup
