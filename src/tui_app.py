"""Bottom-pinned inline TUI for korgex (the reference agent-style architecture).

A **non-full-screen** prompt_toolkit ``Application`` (``full_screen=False``) whose
root ``HSplit`` puts a status bar + the input ``TextArea`` at the bottom. At
startup we print ``height-1`` newlines to scroll the cursor to the last row, so
the whole TUI renders bottom-anchored with empty space above — and the input
stays on the last row while agent output scrolls into the preserved scrollback
above it (routed via ``pt_output``/``run_in_terminal``).

The pure helpers (prompt text, bottom-push math, status line, accept routing) are
tested here; building/running the live ``Application`` is verified by eye and
degrades to the inline ``PromptSession`` REPL when prompt_toolkit isn't usable.
"""
from __future__ import annotations

CARET = "›"


def bottom_push_count(term_lines: int) -> int:
    """How many blank lines to print at startup to push the cursor to the last
    row (so the TUI is bottom-anchored). No-op on a tiny terminal."""
    return term_lines - 1 if term_lines and term_lines > 2 else 0


def prompt_text() -> str:
    """The input prompt — an accent caret."""
    return f"{CARET} "


def status_text(model: str, plan: bool = False) -> str:
    """The bottom status line: model · (plan badge) · hints. Plain text (the live
    app styles it); kept pure so it's testable."""
    badge = " · ◐ PLAN" if plan else ""
    return f" korgex · {model}{badge} · /help · /model · /exit "


def handle_submission(line: str, dispatch) -> None:
    """Route a submitted input line: dispatch non-blank, ignore whitespace-only."""
    if line and line.strip():
        dispatch(line)


def is_available() -> bool:
    """True if prompt_toolkit's Application stack is importable (else the REPL
    falls back to the inline PromptSession path)."""
    try:
        import prompt_toolkit.application  # noqa: F401
        import prompt_toolkit.layout  # noqa: F401
        import prompt_toolkit.widgets  # noqa: F401
        return True
    except Exception:
        return False


def run_app(repl) -> None:
    """Build and run the bottom-pinned Application around a Repl instance.

    `repl` provides: `.model`, `.handle(parse_repl_input(line))`, `._agent`
    (for the live plan badge), and `._banner()`. Output produced by a turn is
    printed via run_in_terminal so it lands in scrollback ABOVE the pinned input.
    Raises ImportError if prompt_toolkit isn't available (caller falls back).
    """
    import shutil

    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import TextArea
    from prompt_toolkit.history import InMemoryHistory

    from src.repl import parse_repl_input

    # Push the whole TUI to the bottom of the terminal (the reference agent's trick).
    try:
        lines = shutil.get_terminal_size((80, 24)).lines
        n = bottom_push_count(lines)
        if n:
            print("\n" * n, end="", flush=True)
    except Exception:
        pass

    repl._banner()

    def _status():
        plan = bool(getattr(repl._agent, "plan_mode_active", False))
        return [("class:status", status_text(repl.model, plan))]

    status_bar = Window(content=FormattedTextControl(_status), height=1,
                        style="class:status")
    input_area = TextArea(
        height=Dimension(min=1, max=8, preferred=1),
        prompt=prompt_text(),
        multiline=True,
        wrap_lines=True,
        history=InMemoryHistory(),
        style="class:input",
    )

    root = HSplit([
        Window(height=Dimension(weight=1)),  # flexible spacer keeps input at bottom
        status_bar,
        Window(height=1, char="─", style="class:rule"),
        input_area,
    ])

    kb = KeyBindings()

    @kb.add("c-c")
    @kb.add("c-d")
    def _quit(event):
        event.app.exit()

    @kb.add("enter")
    def _submit(event):
        text = input_area.text
        input_area.text = ""
        if not (text and text.strip()):
            return
        from prompt_toolkit.application.run_in_terminal import run_in_terminal

        def _do():
            keep = repl.handle(parse_repl_input(text))
            if keep is False:
                event.app.exit()
        run_in_terminal(_do)

    style = Style.from_dict({
        "status": "bg:#0c1117 #8a8f98",
        "rule": "#46525f",
        "input": "#dde6ef",
    })

    app = Application(layout=Layout(root, focused_element=input_area),
                      key_bindings=kb, style=style, full_screen=False,
                      mouse_support=False)
    app.run()
