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


_TIPS = [
    "ask me to build, fix, or explain anything in this repo",
    "/plan — I'll propose a plan read-only before touching files",
    "/model — switch models mid-session (any provider)",
    "drop a SKILL.md in .korgex/skills to teach me a reusable workflow",
]


def dashboard(model: str, cwd: str, version: str, *, providers: list,
              skills: list, mcps: list) -> str:
    """The welcome dashboard that fills the screen on startup: what's connected
    (model · providers), what's available (skills · MCP servers), and quick-start
    tips. Empty sections are skipped — no blank 'Skills: (none)' noise — but a tip
    always nudges how to add them. Plain text; `render` paints it with color."""
    L = []
    L.append(f"  model    {model}")
    L.append(f"  cwd      {_short_cwd(cwd)}")
    if providers:
        L.append(f"  providers {' · '.join(providers)}")
    L.append("")

    if skills:
        L.append("  skills")
        for name, desc in skills[:6]:
            L.append(f"    ◆ {name} — {desc}")
        L.append("")
    if mcps:
        L.append("  mcp servers")
        L.append("    " + " · ".join(mcps))
        L.append("")

    L.append("  try")
    for tip in _TIPS:
        L.append(f"    › {tip}")
    return "\n".join(L)


def render_dashboard(model: str, cwd: str, version: str, *, providers, skills,
                     mcps, out=None) -> None:
    """Paint the welcome dashboard with rich color (sections dim, accents bright)."""
    text = dashboard(model, cwd, version, providers=providers, skills=skills, mcps=mcps)
    try:
        from rich.console import Console
        from rich.text import Text
        console = Console(file=out) if out is not None else Console()
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped in ("skills", "mcp servers", "try"):
                console.print(Text(line, style="bold #ffcf6b"))  # section headers, amber
            elif stripped.startswith("◆"):
                console.print(Text(line, style="#a5de67"))        # skills, green
            elif stripped.startswith("›"):
                console.print(Text(line, style="dim"))            # tips, dim
            else:
                console.print(Text(line, style="#8a8f98"))        # meta, gray
        console.print()
    except Exception:
        print(text, file=out)
        print(file=out)


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
