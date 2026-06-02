"""Project-rules hierarchy — layered AGENTS.md / .korgex/rules, merged.

A single root ``AGENTS.md`` doesn't match how real repos work: a monorepo root
sets house style, a package refines it, a ``.korgex/rules/`` dir holds focused
modular rules, and a developer keeps personal defaults in ``~/.korgex/AGENTS.md``.
This collects them in precedence order and merges them into one prompt block:

    1. user-global   ~/.korgex/AGENTS.md
    2. the directory chain from the GIT ROOT down to the launch dir (bounded —
       never reads above the repo), AGENTS.md (or CLAUDE.md) per level
    3. <repo>/.korgex/rules/*.md   (sorted)

Least-specific first, most-specific last, so the closer rule reads last. Zero-dep,
best-effort: an unreadable file is skipped, never fatal.
"""
from __future__ import annotations

import os


def _read(path: str) -> str:
    try:
        return open(path).read().strip()
    except OSError:
        return ""


def _git_root(start: str):
    """The nearest ancestor (incl. `start`) containing a ``.git`` — the repo
    boundary we never read above. None if there's no git repo."""
    cur = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _dir_chain(repo_root: str) -> list:
    """Directories from the git root DOWN to `repo_root` (inclusive), top first.
    Bounded by the git root; when there's no repo, just `[repo_root]`."""
    start = os.path.abspath(repo_root)
    top = _git_root(start) or start
    chain, cur = [start], start
    while cur != top:
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        chain.append(parent)
        cur = parent
    chain.reverse()                       # top (least specific) → start (most specific)
    return chain


def collect_rule_files(repo_root: str, home: str = None) -> list:
    """Ordered ``[(label, path)]`` of every rule file that applies, least-specific
    first. `home` defaults to the real home dir (override in tests)."""
    home = home if home is not None else os.path.expanduser("~")
    out = []

    # 1. user-global
    g = os.path.join(home, ".korgex", "AGENTS.md")
    if os.path.isfile(g):
        out.append(("user-global", g))

    # 2. the git-bounded directory chain, one rule file per level (AGENTS preferred)
    root_abs = os.path.abspath(repo_root)
    for d in _dir_chain(repo_root):
        for fname in ("AGENTS.md", "CLAUDE.md"):
            p = os.path.join(d, fname)
            if os.path.isfile(p):
                label = fname if d == root_abs else f"{os.path.basename(d)}/{fname}"
                out.append((label, p))
                break                     # one file per directory

    # 3. modular .korgex/rules/*.md (sorted)
    rules_dir = os.path.join(repo_root, ".korgex", "rules")
    if os.path.isdir(rules_dir):
        for name in sorted(os.listdir(rules_dir)):
            if name.endswith(".md"):
                out.append((f"rules/{name}", os.path.join(rules_dir, name)))

    return out


def load_project_rules(repo_root: str, home: str = None) -> str:
    """Merge every applicable rule file into one prompt block (least-specific
    first). Empty string when there are no rules, so the caller can skip the
    section entirely."""
    parts = []
    for label, path in collect_rule_files(repo_root, home=home):
        content = _read(path)
        if content:
            parts.append(f"# Project instructions ({label})\n\n{content}")
    return "\n\n".join(parts)
