"""Custom slash commands — user- and project-definable prompts, markdown-defined.

A command is a markdown file with optional frontmatter::

    ---
    description: Review code — local changes or a PR
    argument-hint: [pr-number | blank for local]
    ---
    # Code Review
    Review $ARGUMENTS ...

Invoke it in the REPL as ``/<filename>`` (e.g. ``/code-review 42``). The body — with
``$ARGUMENTS`` and ``$1..$9`` substituted — becomes the turn prompt. Roots are searched
built-in (lowest) → project (``.korgex/commands``) → user (``~/.korgex/commands``, highest),
mirroring the skills loader, so projects and users add or override commands without forking.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from src.skills import _parse_frontmatter  # reuse the exact frontmatter parser skills use


@dataclass
class Command:
    name: str
    description: str = ""
    argument_hint: str = ""
    body: str = ""
    path: str = ""
    source: str = ""


def parse_command(md_path: str):
    """Parse one ``.md`` into a Command, or None if unreadable. Unlike skills, no
    frontmatter is required — the name comes from the filename; description/argument-hint
    are optional metadata."""
    try:
        with open(md_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    meta, body = _parse_frontmatter(text)
    meta = meta or {}
    name = os.path.basename(md_path)
    if name.endswith(".md"):
        name = name[:-3]
    return Command(
        name=name,
        description=str(meta.get("description", "")).strip(),
        argument_hint=str(meta.get("argument-hint", meta.get("argument_hint", ""))).strip(),
        body=(body or text).strip(),
        path=md_path,
    )


class CommandRegistry:
    """An in-memory set of loaded commands, keyed by name."""

    def __init__(self, commands=None):
        self._by_name = {c.name: c for c in (commands or [])}

    def names(self) -> list:
        return sorted(self._by_name)

    def get(self, name: str):
        return self._by_name.get(name)

    def all(self) -> list:
        return [self._by_name[n] for n in self.names()]


def load_commands(roots) -> CommandRegistry:
    """Scan each root for ``*.md`` and build a registry. Missing roots are skipped.
    Later roots override earlier ones on a name clash (user shadows project shadows built-in)."""
    by_name = {}
    for root in roots or []:
        if not root or not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            if not entry.endswith(".md"):
                continue
            cmd = parse_command(os.path.join(root, entry))
            if cmd:
                cmd.source = root
                by_name[cmd.name] = cmd
    return CommandRegistry(list(by_name.values()))


def builtin_commands_root() -> str:
    """The baseline command set shipped with korgex."""
    return os.path.join(os.path.dirname(__file__), "commands_builtin")


def default_command_roots(repo_root: str | None = None) -> list:
    """Command roots in PRECEDENCE order (later wins): built-in → project → user-global."""
    roots = [builtin_commands_root()]
    if repo_root:
        roots.append(os.path.join(repo_root, ".korgex", "commands"))
    roots.append(os.path.join(os.path.expanduser("~"), ".korgex", "commands"))
    return roots


def render_command(cmd: Command, args: str = "") -> str:
    """Substitute ``$1..$9`` (positional words) then ``$ARGUMENTS`` (the whole string) in
    the command body. Unfilled positionals are left as-is."""
    parts = args.split()

    def _pos(m):
        i = int(m.group(1))
        return parts[i - 1] if 1 <= i <= len(parts) else m.group(0)

    body = re.sub(r"\$([1-9])", _pos, cmd.body)
    return body.replace("$ARGUMENTS", args)
