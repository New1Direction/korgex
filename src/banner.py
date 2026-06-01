"""Startup banner — korgex's designed boot screen.

A wordmark + a status line (model · cwd · version) + a command hint, so launching
korgex feels like a real agent CLI instead of one bare line. The text assembly is
pure (testable); `render()` paints it with rich (color), falling back to plain
text where rich isn't available.

The wordmark is korgex's own identity — block-letter ASCII, no third-party art.
"""
from __future__ import annotations

import os

# Block-letter "korgex" wordmark — korgex's own figlet-style design (no box, so
# rows can't misalign), vendor-neutral.
_WORDMARK = r"""
 ██  ██  ██████  ██████   ██████  ██████ ██  ██
 ██ ██   ██  ██  ██  ██   ██      ██      ████
 ████    ██  ██  ██████   ██  ███ ████     ██
 ██ ██   ██  ██  ██  ██   ██   ██ ██      ████
 ██  ██  ██████  ██  ██   ██████  ██████ ██  ██""".strip("\n")

_TAGLINE = "the cross-vendor coding agent — every model, one tool, provable"


def wordmark() -> str:
    """The multi-row ASCII wordmark (plain string)."""
    return _WORDMARK


def _short_cwd(cwd: str) -> str:
    home = os.path.expanduser("~")
    if cwd == home:
        return "~"
    if cwd.startswith(home + os.sep):
        return "~" + cwd[len(home):]
    return cwd


def status_line(model: str, cwd: str, version: str) -> str:
    """One status line: model · cwd · version, dot-separated."""
    return f"model {model}  ·  {_short_cwd(cwd)}  ·  v{version}"


def hint_line() -> str:
    """The always-visible command hint."""
    return "/help commands   ·   /model switch   ·   /plan read-only   ·   /exit"


def startup_text(model: str, cwd: str, version: str, configured: bool = True) -> str:
    """The full boot text: wordmark + tagline + status + hint (plain). When no
    provider is connected yet, nudge `korgex setup` instead of the status line."""
    parts = [_WORDMARK, "", " " + _TAGLINE, ""]
    if configured:
        parts.append(" " + status_line(model, cwd, version))
    else:
        parts.append(" no model connected yet — run `korgex setup` to connect a provider")
    parts.append(" " + hint_line())
    return "\n".join(parts)


def render(model: str, cwd: str, version: str, configured: bool = True, out=None) -> None:
    """Paint the banner with rich color (amber wordmark, dim status), falling back
    to plain text if rich isn't importable or output isn't a console."""
    try:
        from rich.console import Console
        from rich.text import Text
        console = Console(file=out) if out is not None else Console()
        wm = Text(_WORDMARK, style="bold yellow")
        console.print(wm)
        console.print(Text(" " + _TAGLINE, style="italic dim"))
        if configured:
            console.print(Text(" " + status_line(model, cwd, version), style="dim"))
        else:
            console.print(Text(" no model connected yet — run `korgex setup` to connect a provider",
                               style="yellow"))
        console.print(Text(" " + hint_line(), style="dim"))
        console.print()
    except Exception:
        print(startup_text(model, cwd, version, configured), file=out)
        print(file=out)
