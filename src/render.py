"""Clean block-rendered output for the interactive REPL.

Replaces raw streamed text with structured blocks: each message gets a left
**accent bar** (``▎``) tinted per role, with content beside it; tool calls render
as a compact ``◆ verb  target  (dim details)`` line. A central ``Theme`` holds the
semantic accent colors so call sites never hardcode a color.

All functions return rich-markup strings (paint them via ``pt_output.render_rich``
+ ``emit``), so the formatting is pure and testable; the terminal paint is not.

The design language — per-role accent blocks, diamond tool bullets, dimmed
details, truncation — is recreated generically from accent-block agent TUIs.
"""
from __future__ import annotations

import os

ACCENT_BAR = "▎"
TOOL_BULLET = "◆"

# Semantic accent colors per role (rich color names / hex). One place to retint.
_ACCENTS = {
    "user": "#5fd0ff",       # cyan — your turns
    "assistant": "#a5de67",  # green — korgex's prose
    "tool": "#a89bff",       # violet — tool activity
    "thinking": "#808a96",   # gray — reasoning
    "error": "#ff6b6b",      # red
    "success": "#78c878",    # green
    "system": "#8a8f98",     # dim gray
}
_DEFAULT_ACCENT = "#8a8f98"

# How a tool's target is found in its args (first match wins).
_TARGET_KEYS = ("file_path", "filepath", "path", "notebook_path", "command",
                "pattern", "query", "url", "to")

# Map a tool name to a short lowercase verb for the block header.
_VERB = {
    "Read": "read", "Write": "write", "Edit": "edit", "MultiEdit": "edit",
    "Bash": "run", "Grep": "grep", "Glob": "glob", "ToolSearch": "search",
    "Recall": "recall", "Skill": "skill", "BusSend": "send", "BusInbox": "inbox",
    "NotebookEdit": "edit",
}


class Theme:
    """Semantic color slots for the renderer. Swap the table to retheme; call
    sites ask for ``accent(role)`` and never hardcode a color."""

    def __init__(self, accents: dict | None = None):
        self._accents = dict(_ACCENTS)
        if accents:
            self._accents.update(accents)

    def accent(self, role: str) -> str:
        return self._accents.get(role, _DEFAULT_ACCENT)


_THEME = Theme()


def block(role: str, content: str, label: str | None = None, theme: Theme | None = None) -> str:
    """Render `content` as a left-accent block: every line carries a ``▎`` bar in
    the role's accent color; an optional dim `label` heads the block."""
    th = theme or _THEME
    color = th.accent(role)
    bar = f"[{color}]{ACCENT_BAR}[/{color}]"
    lines = []
    if label:
        lines.append(f"{bar} [bold {color}]{label}[/bold {color}]")
    for ln in (content or "").split("\n"):
        lines.append(f"{bar} {ln}")
    return "\n".join(lines)


def _shorten_target(target: str, keep: int = 48) -> str:
    """Clamp a long target, keeping the meaningful tail (e.g. the filename)."""
    if len(target) <= keep:
        return target
    base = os.path.basename(target.rstrip("/"))
    return ("…/" + base) if base else (target[: keep - 1] + "…")


def tool_target(name: str, args: dict) -> str:
    """The one meaningful argument to show for a tool call (path, command, …)."""
    if isinstance(args, dict):
        for k in _TARGET_KEYS:
            if args.get(k):
                return str(args[k])
    return ""


def echo_user(text: str, theme: Theme | None = None) -> str:
    """Render the user's submitted turn as a ``▎ you`` block, so the conversation
    reads as an exchange (your turn, then the reply) in scrollback."""
    return block("user", text, label="you", theme=theme)


def render_markdown(text: str, width: int | None = None) -> str:
    """Render markdown (headings, bold, lists, fenced code with highlighting) to an
    ANSI string. Used to re-render the assistant's COMPLETED prose — markdown can't
    be rendered mid-stream (you can't format half a code fence), so the stream is
    plain and this paints the final buffer. Falls back to the raw text on any error."""
    if not text:
        return ""
    try:
        import shutil
        from io import StringIO
        from rich.console import Console
        from rich.markdown import Markdown
        buf = StringIO()
        w = width or shutil.get_terminal_size((80, 24)).columns
        Console(file=buf, force_terminal=True, color_system="truecolor",
                width=w).print(Markdown(text))
        return buf.getvalue()
    except Exception:
        return text


class StreamBlock:
    """Render streamed tokens as a live accent block: a ``▎ label`` header on the
    first chunk, then the accent bar continued at the start of every new line as
    tokens arrive. `sink(markup_str)` receives rich-markup chunks to paint.

    This is what makes a STREAMING reply read like a structured block (à la the
    accent-block agent TUIs) instead of a flat wall of text."""

    def __init__(self, role: str, label: str | None = None, sink=None,
                 theme: Theme | None = None):
        th = theme or _THEME
        self._color = th.accent(role)
        self._bar = f"[{self._color}]{ACCENT_BAR}[/{self._color}] "
        self._label = label
        self._sink = sink or (lambda s: None)
        self._started = False
        self._at_line_start = True
        self._closed = False
        self.buffer = ""  # raw text fed so far (for an optional markdown re-render)

    def _open(self):
        if self._started:
            return
        self._started = True
        if self._label:
            self._sink(f"\n{self._bar.rstrip()} [bold {self._color}]{self._label}[/bold {self._color}]\n")
        self._sink(self._bar)         # bar for the first content line
        self._at_line_start = False

    def feed(self, chunk: str):
        """Emit a streamed chunk, inserting the accent bar after each newline."""
        if self._closed or not chunk:
            return
        self.buffer += chunk
        self._open()
        # Split keeping the structure: each '\n' starts a new bar-prefixed line.
        parts = chunk.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                self._sink("\n" + self._bar)
            if part:
                self._sink(part)

    def close(self):
        """Finish the block (idempotent). Adds a trailing newline once."""
        if self._closed or not self._started:
            self._closed = True
            return
        self._sink("\n")
        self._closed = True


def truncate_output(text: str, first: int = 2, last: int = 3) -> str:
    """Collapse long output to its first `first` + last `last` lines with a dim
    ``… N lines hidden …`` marker between — the head+tail pattern that keeps tool
    output / thinking readable. Short output (≤ first+last lines) is returned
    unchanged. `first=0` keeps only the tail (good for thinking)."""
    lines = (text or "").rstrip("\n").split("\n")
    if len(lines) <= first + last:
        return "\n".join(lines)
    hidden = len(lines) - first - last
    head = lines[:first] if first > 0 else []
    tail = lines[len(lines) - last:] if last > 0 else []
    marker = f"[dim]… {hidden} lines hidden …[/dim]"
    return "\n".join(head + [marker] + tail)


def tool_line(name: str, args: dict, detail: str | None = None,
              theme: Theme | None = None) -> str:
    """A compact tool-call line: ``◆ verb  target  (dim detail)`` — the diamond
    + verb in the tool accent, the target plain, parenthetical details dimmed."""
    th = theme or _THEME
    color = th.accent("tool")
    verb = _VERB.get(name, name.lower())
    target = _shorten_target(tool_target(name, args))
    out = f"  [{color}]{TOOL_BULLET}[/{color}] [{color}]{verb:<7}[/{color}] {target}".rstrip()
    if detail:
        out += f"  [dim]({detail})[/dim]"
    return out
