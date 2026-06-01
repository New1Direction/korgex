"""Startup banner — korgex's designed boot screen.

A wordmark + a status line (model · cwd · version) + a command hint, so launching
korgex feels like a real agent CLI instead of one bare line. The text assembly is
pure (testable); `render()` paints it with rich (color), falling back to plain
text where rich isn't available.

The wordmark is korgex's own identity — block-letter ASCII, no third-party art.
"""
from __future__ import annotations

import os

# Block-letter "KORGEX" wordmark — korgex's own design in heavy box-drawing
# glyphs (██╗/╚═╝) so it reads with depth/shadow. Painted with a 3-tier gold→bronze
# gradient in `render` (the rows map to _WORDMARK_TIERS) for the 3D look.
_WORDMARK = r"""
██╗  ██╗ ██████╗ ██████╗  ██████╗ ███████╗██╗  ██╗
██║ ██╔╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝╚██╗██╔╝
█████╔╝ ██║   ██║██████╔╝██║  ███╗█████╗   ╚███╔╝
██╔═██╗ ██║   ██║██╔══██╗██║   ██║██╔══╝   ██╔██╗
██║  ██╗╚██████╔╝██║  ██║╚██████╔╝███████╗██╔╝ ██╗
╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝""".strip("\n")

# Per-row gradient palettes (top→bottom) for the wordmark's 3D depth. 'red' is the
# default (bright crimson fading to deep red); 'gold' is the original.
_PALETTES = {
    "red":  ["#ff5b6e", "#ff3b54", "#e92846", "#c81f3a", "#a3162e", "#7d1023"],
    "gold": ["#ffd700", "#ffd700", "#ffbf00", "#f0a020", "#cd7f32", "#cd7f32"],
}
_DEFAULT_PALETTE = "red"


def wordmark_tiers(palette: str = _DEFAULT_PALETTE) -> list:
    """The per-row color gradient for the wordmark (defaults to the red palette)."""
    return _PALETTES.get(palette, _PALETTES[_DEFAULT_PALETTE])


def center_block(text: str, width: int) -> str:
    """Center each line of a multi-line block within `width` columns (for the
    calm, Grok-style centered wordmark)."""
    lines = text.split("\n")
    block_w = max((len(ln) for ln in lines), default=0)
    pad = max(0, (width - block_w) // 2)
    return "\n".join((" " * pad) + ln for ln in lines)


# back-compat alias used by older callers/tests
_WORDMARK_TIERS = _PALETTES["gold"]

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


def summary_line(skills: int, mcps: int, tools: int) -> str:
    """The footer tally: '12 tools · 5 skills · 3 MCP servers · /help'."""
    return f"{tools} tools · {skills} skills · {mcps} MCP servers · /help for commands"


def categorize_skills(skills: list) -> dict:
    """Group skills by their name prefix (``github-auth``/``github-codegen`` →
    ``github``), so the welcome lists read as tidy categories like the reference
    TUI. A bare name with no ``-`` lands in a ``general`` bucket, never dropped."""
    groups: dict = {}
    for name, _desc in skills:
        cat = name.split("-", 1)[0] if "-" in name else "general"
        groups.setdefault(cat, []).append(name)
    return groups


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


# Small mascot — korgex's own ASCII (a stylized 'k' shield), left column of the panel.
_MASCOT = r"""[#ffcf6b]    ▄▄▄▄▄    [/]
[#ffcf6b]  ▟█▀▀▀█▙  [/]
[#f0a020] ██  ▟█▘  [/]
[#f0a020] ██ ▜█▖   [/]
[#cd7f32] ▜█▄▄▄█▛  [/]
[#cd7f32]   ▀▀▀▀▀   [/]"""


def render_dashboard(model: str, cwd: str, version: str, *, providers, skills,
                     mcps, tools: int = 0, out=None) -> None:
    """Paint the welcome panel: a bordered box with a mascot column (left) and
    categorized model/providers/skills/MCP lists (right), plus a summary footer —
    the structured, framed look of a polished agent CLI. Falls back to the plain
    `dashboard` text if rich isn't available."""
    try:
        from rich.console import Console, Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from rich import box

        console = Console(file=out) if out is not None else Console()

        # Right column: what's connected + available, grouped by category.
        right = []
        right.append(Text(f"model     {model}", style="#dde6ef"))
        right.append(Text(f"cwd       {_short_cwd(cwd)}", style="#8a8f98"))
        if providers:
            right.append(Text(f"providers {' · '.join(providers)}", style="#5fd0ff"))
        if mcps:
            right.append(Text(""))
            right.append(Text("MCP Servers", style="bold #ffcf6b"))
            right.append(Text("  " + " · ".join(mcps), style="#a89bff"))
        if skills:
            right.append(Text(""))
            right.append(Text("Available Skills", style="bold #ffcf6b"))
            for cat, names in sorted(categorize_skills(skills).items()):
                shown = ", ".join(names[:4])
                more = f", +{len(names) - 4}" if len(names) > 4 else ""
                right.append(Text.assemble((f"  {cat}: ", "#808a96"),
                                           (shown + more, "#a5de67")))
        right.append(Text(""))
        right.append(Text("try", style="bold #ffcf6b"))
        for tip in _TIPS:
            right.append(Text(f"  › {tip}", style="dim"))
        right.append(Text(""))
        right.append(Text(summary_line(len(skills), len(mcps), tools), style="dim"))

        grid = Table.grid(padding=(0, 3))
        grid.add_column(vertical="top")   # mascot
        grid.add_column(vertical="top")   # content
        grid.add_row(Text.from_markup(_MASCOT), Group(*right))

        title = f"korgex v{version} · cross-vendor coding agent"
        console.print(Panel(grid, title=title, title_align="left",
                            border_style="#46525f", box=box.ROUNDED, padding=(1, 2)))
        console.print()
    except Exception:
        print(dashboard(model, cwd, version, providers=providers, skills=skills, mcps=mcps),
              file=out)
        print(file=out)


def render_wordmark(console, palette: str = _DEFAULT_PALETTE, center: bool = True) -> None:
    """Paint the wordmark with a per-row gradient (default: red) for 3D depth,
    centered in the terminal by default (the calm, Grok-style look)."""
    from rich.text import Text
    from rich.align import Align
    tiers = wordmark_tiers(palette)
    rows = _WORDMARK.split("\n")
    for i, row in enumerate(rows):
        color = tiers[min(i, len(tiers) - 1)]
        line = Text(row, style=f"bold {color}")
        console.print(Align.center(line) if center else line)


def render(model: str, cwd: str, version: str, configured: bool = True, out=None) -> None:
    """Paint the gradient wordmark + tagline + status, falling back to plain text
    if rich isn't importable."""
    try:
        from rich.console import Console
        from rich.text import Text
        console = Console(file=out) if out is not None else Console()
        render_wordmark(console)
        console.print(Text(" " + _TAGLINE, style="italic dim"))
        if not configured:
            console.print(Text(" no model connected yet — run `korgex setup` to connect a provider",
                               style="yellow"))
        console.print()
    except Exception:
        print(startup_text(model, cwd, version, configured), file=out)
        print(file=out)
