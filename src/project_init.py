"""`korgex init` — scaffold a project's AGENTS.md.

Bootstraps the context file that the project-rules hierarchy (src/project_rules.py)
reads every session. It detects the stack and the test/build commands so the file
starts useful instead of empty, leaves placeholders for the human judgment a tool
can't infer (overview, conventions), and never clobbers an existing AGENTS.md.

Detection + rendering are pure; only ``scaffold`` touches disk.
"""
from __future__ import annotations

import os

# (manifest filename, language label, test command, build command). Order matters:
# the first detected language supplies the default test command.
_STACKS = [
    ("pyproject.toml", "Python", "pytest -q", None),
    ("setup.py", "Python", "pytest -q", None),
    ("package.json", "JavaScript/TypeScript", "npm test", "npm run build"),
    ("Cargo.toml", "Rust", "cargo test", "cargo build"),
    ("go.mod", "Go", "go test ./...", "go build ./..."),
]


def detect_stack(repo_root: str) -> dict:
    """Sniff the repo's languages + test/build commands from its manifest files."""
    facts = {"languages": [], "manifests": [], "test_cmd": None, "build_cmd": None}
    for manifest, lang, test_cmd, build_cmd in _STACKS:
        if not os.path.isfile(os.path.join(repo_root, manifest)):
            continue
        if lang not in facts["languages"]:
            facts["languages"].append(lang)
        facts["manifests"].append(manifest)
        if facts["test_cmd"] is None:
            facts["test_cmd"] = test_cmd
        if facts["build_cmd"] is None and build_cmd:
            facts["build_cmd"] = build_cmd
    return facts


def render_agents_md(facts: dict, project_name: str) -> str:
    """Render a starter AGENTS.md from detected facts, with placeholders for the
    parts only a human knows (overview, conventions)."""
    langs = ", ".join(facts.get("languages") or []) or "—"
    manifests = ", ".join(facts.get("manifests") or []) or "—"
    test_cmd = facts.get("test_cmd") or "TODO: add the test command"
    build_cmd = facts.get("build_cmd") or "TODO: add the build command"
    return f"""# {project_name}

> Project guide for AI coding agents (read automatically by korgex and compatible tools).

## Overview
TODO: one or two sentences on what this project is and does.

## Stack
- Languages: {langs}
- Manifests: {manifests}

## Commands
- Test: `{test_cmd}`
- Build: `{build_cmd}`

## Conventions
- TODO: coding conventions an agent should follow (style, patterns, what to avoid).

## Notes
- TODO: anything non-obvious about this codebase worth knowing up front.
"""


def scaffold(repo_root: str) -> dict:
    """Write AGENTS.md for `repo_root` if absent. Never clobbers an existing one.
    Returns ``{written, path, reason}``."""
    path = os.path.join(repo_root, "AGENTS.md")
    if os.path.exists(path):
        return {"written": False, "path": path, "reason": "AGENTS.md already exists"}
    facts = detect_stack(repo_root)
    name = os.path.basename(os.path.abspath(repo_root)) or "project"
    with open(path, "w") as f:
        f.write(render_agents_md(facts, name))
    return {"written": True, "path": path, "reason": "created", "facts": facts}
