"""The ECC-sourced skill pack must load cleanly as built-in skills.

These were cherry-picked from github.com/affaan-m/ECC (MIT — see src/skills_builtin/CREDITS.md)
and dropped into src/skills_builtin/ as directory-layout skills. korgex's loader requires
valid `name:` + `description:` frontmatter, so this pins that they all parse.
"""
from __future__ import annotations

from src.skills import builtin_skills_root, load_skills

ECC_SKILLS = [
    "accessibility", "agent-eval", "agent-introspection-debugging", "agentic-engineering",
    "ai-first-engineering", "ai-regression-testing", "api-connector-builder",
    "architecture-decision-records", "autonomous-loops", "backend-patterns",
    "benchmark-optimization-loop", "browser-qa", "code-tour", "codebase-onboarding",
    "coding-standards",
]


def test_ecc_skills_load_with_valid_frontmatter():
    reg = load_skills([builtin_skills_root()])
    for name in ECC_SKILLS:
        sk = reg.get(name)
        assert sk is not None, f"ECC skill {name!r} did not load (missing name/description?)"
        assert sk.description, f"ECC skill {name!r} has no description"
        assert sk.body, f"ECC skill {name!r} has an empty body"


def test_builtin_library_grew():
    reg = load_skills([builtin_skills_root()])
    assert len(reg.names()) >= 49   # 34 original + 15 ECC pack
