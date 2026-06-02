"""Skills — file-defined, progressively-disclosed reusable workflows.

A skill is a directory containing a ``SKILL.md``:

    ---
    name: fix-flaky
    description: find and fix flaky tests
    version: 1.0
    trust: user          # built-in | user | installed | untrusted
    ---
    <the instruction body the agent follows when this skill is invoked>

The loader indexes every skill it finds and the agent injects a COMPACT INDEX
(name + one-line description only) into the system prompt. The body is withheld
until the skill is actually invoked — progressive disclosure keeps the prompt
small no matter how many skills exist. The ``trust`` tier marks provenance so a
future self-improvement/curator pass only ever auto-edits agent-created skills,
never user/built-in ones.

Frontmatter is parsed with a tiny zero-dep ``key: value`` reader (no YAML
dependency — korgex stays installable without extras).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_TRUST = "user"
# "agent" = a skill korgex wrote itself (self-learning). The curator/lifecycle ONLY
# ever auto-edit or age `agent` skills; user/built-in/installed are never touched.
VALID_TRUST = ("built-in", "user", "installed", "untrusted", "agent")


@dataclass
class Skill:
    name: str
    description: str
    body: str
    version: str = "1.0"
    trust: str = _DEFAULT_TRUST
    path: str = ""


def _parse_frontmatter(text: str):
    """Split a ``---\\n…\\n---\\n body`` doc into ({key:value}, body). Returns
    (None, _) if there's no leading frontmatter block."""
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines()
    # find the closing '---'
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        return None, text
    meta = {}
    for ln in lines[1:close]:
        if ":" in ln:
            k, _, v = ln.partition(":")
            meta[k.strip().lower()] = v.strip()
    body = "\n".join(lines[close + 1:]).strip()
    return meta, body


def parse_skill(skill_md_path: str):
    """Parse one SKILL.md into a Skill, or None if it lacks valid frontmatter
    (name + description are required)."""
    try:
        text = open(skill_md_path).read()
    except OSError:
        return None
    meta, body = _parse_frontmatter(text)
    if not meta or not meta.get("name") or not meta.get("description"):
        return None
    trust = meta.get("trust", _DEFAULT_TRUST)
    if trust not in VALID_TRUST:
        trust = _DEFAULT_TRUST
    return Skill(
        name=meta["name"], description=meta["description"], body=body,
        version=meta.get("version", "1.0"), trust=trust, path=skill_md_path,
    )


class SkillRegistry:
    """An in-memory set of loaded skills, keyed by name."""

    def __init__(self, skills=None):
        self._skills = {s.name: s for s in (skills or [])}

    def names(self) -> list:
        return sorted(self._skills)

    def get(self, name: str):
        return self._skills.get(name)

    def index_block(self) -> str:
        """A compact prompt block: one ``- name — description`` line per skill.
        Bodies are NOT included (loaded on demand via invoke). Empty string when
        there are no skills, so the caller can skip the section entirely."""
        if not self._skills:
            return ""
        lines = ["# Available skills (invoke with the Skill tool by name):"]
        for name in self.names():
            s = self._skills[name]
            lines.append(f"- {name} — {s.description}")
        return "\n".join(lines)


def load_skills(roots) -> SkillRegistry:
    """Scan each root directory for ``*/SKILL.md`` and build a registry. Missing
    roots are skipped. Later roots override earlier ones on a name clash (so a
    user skill can shadow a built-in of the same name)."""
    found = []
    for root in roots or []:
        if not root or not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            md = os.path.join(root, entry, "SKILL.md")
            if os.path.isfile(md):
                sk = parse_skill(md)
                if sk:
                    found.append(sk)
    return SkillRegistry(found)


def builtin_skills_root() -> str:
    """The baseline skill library shipped with korgex (trust: built-in)."""
    return os.path.join(os.path.dirname(__file__), "skills_builtin")


def default_skill_roots(repo_root: str | None = None) -> list:
    """Where skills live, in PRECEDENCE order (later roots win on a name clash):
    bundled built-ins (lowest) → project → user-global (highest). So a user or
    project skill can shadow a built-in by reusing its name."""
    roots = [builtin_skills_root()]
    if repo_root:
        roots.append(os.path.join(repo_root, ".korgex", "skills"))
    roots.append(os.path.join(os.path.expanduser("~"), ".korgex", "skills"))
    return roots


def invoke_skill(registry: SkillRegistry, name: str, args: str = "") -> dict:
    """Resolve a skill by name and return its body for the agent to follow.
    Unknown name → an error (never guess), matching the Skill tool's contract."""
    sk = registry.get(name)
    if sk is None:
        avail = ", ".join(registry.names()) or "(none)"
        return {"ok": False, "error": f"unknown skill '{name}'. available: {avail}"}
    header = f"# Skill: {sk.name} (v{sk.version})\n"
    if args:
        header += f"# Arguments: {args}\n"
    return {"ok": True, "name": sk.name, "body": header + "\n" + sk.body}
