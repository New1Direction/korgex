"""mise project-task auto-discovery.

If a repo uses [mise](https://github.com/jdx/mise), it declares the real build/test/
lint commands as **mise tasks**. Surfacing them in the agent's context lets korgex run
the project's actual commands (``mise run test``) instead of guessing — zero config,
no MCP server required (that's the separate ``korgex mcp add mise`` path).

The ``mise tasks ls --json`` subprocess is injected so the parsing/rendering is fully
unit-testable offline; everything degrades to "no tasks" on any error, so a missing
mise binary or a malformed response never breaks prompt assembly.
"""
from __future__ import annotations

import json
import os
import subprocess

_CONFIG_NAMES = ("mise.toml", ".mise.toml", os.path.join(".config", "mise", "config.toml"))


def detect(repo_root: str) -> bool:
    """True if the repo uses mise (a mise config file is present)."""
    for name in _CONFIG_NAMES:
        if os.path.isfile(os.path.join(repo_root, name)):
            return True
    return False


def _run_mise(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=10)  # noqa: S603
    return r.stdout


def list_tasks(repo_root: str, *, run=None) -> list:
    """Parse ``mise tasks ls --json`` into ``[{name, description}, …]``. ``run(cmd, cwd)
    -> str`` is injected for tests (default: the real subprocess). Returns ``[]`` on
    any error (missing binary, non-zero exit, malformed JSON)."""
    runner = run or _run_mise
    try:
        raw = runner(["mise", "tasks", "ls", "--json"], repo_root)
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("tasks")
    if not isinstance(data, list):
        return []
    out = []
    for t in data:
        if isinstance(t, dict) and t.get("name"):
            out.append({"name": t["name"], "description": t.get("description") or ""})
    return out


def render_block(tasks) -> str:
    """A concise system-prompt block listing the project's mise tasks, or '' if none."""
    if not tasks:
        return ""
    lines = "\n".join(
        f"- `mise run {t['name']}`" + (f" — {t['description']}" if t.get("description") else "")
        for t in tasks
    )
    return ("# Project tasks (mise)\n"
            "This repo declares its build/test/lint commands as mise tasks. Prefer these "
            "over guessing the commands:\n" + lines)


def project_task_block(repo_root: str, *, run=None) -> str:
    """Detect + list + render in one call. Returns '' when mise isn't used here or has
    no tasks (and skips the subprocess entirely when no mise config is present)."""
    if not detect(repo_root):
        return ""
    return render_block(list_tasks(repo_root, run=run))
