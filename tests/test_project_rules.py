"""Tests for the project-rules hierarchy (src/project_rules.py).

korgex used to read ONE root AGENTS.md. Real repos layer conventions: a monorepo
root sets house style, a package refines it, and a `.korgex/rules/` dir holds
focused modular rules. This module collects them all — user-global → up the dir
tree (bounded by the git root, never above it) → repo root → .korgex/rules — and
merges them into one block, least-specific first so the closer, more-specific rule
reads last.
"""
import os

from src import project_rules as PR


def _w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _nohome(tmp_path):
    return str(tmp_path / "_no_home_")   # a home dir with no ~/.korgex/AGENTS.md


# ── single root (back-compat with the old one-file behavior) ─────────────────

class TestSingleRoot:
    def test_root_agents_md_is_included(self, tmp_path):
        _w(str(tmp_path / "AGENTS.md"), "Always run ruff before committing.")
        block = PR.load_project_rules(str(tmp_path), home=_nohome(tmp_path))
        assert "Always run ruff before committing" in block

    def test_claude_md_is_a_fallback(self, tmp_path):
        _w(str(tmp_path / "CLAUDE.md"), "Use four-space indent.")
        block = PR.load_project_rules(str(tmp_path), home=_nohome(tmp_path))
        assert "Use four-space indent" in block

    def test_agents_preferred_when_both_present(self, tmp_path):
        _w(str(tmp_path / "AGENTS.md"), "AGENTS WINS")
        _w(str(tmp_path / "CLAUDE.md"), "CLAUDE LOSES")
        block = PR.load_project_rules(str(tmp_path), home=_nohome(tmp_path))
        assert "AGENTS WINS" in block and "CLAUDE LOSES" not in block

    def test_empty_when_nothing_exists(self, tmp_path):
        assert PR.load_project_rules(str(tmp_path), home=_nohome(tmp_path)) == ""


# ── .korgex/rules/*.md ───────────────────────────────────────────────────────

class TestRulesDir:
    def test_rules_files_included_in_sorted_order(self, tmp_path):
        _w(str(tmp_path / ".korgex" / "rules" / "01-style.md"), "STYLE RULE")
        _w(str(tmp_path / ".korgex" / "rules" / "02-tests.md"), "TESTS RULE")
        block = PR.load_project_rules(str(tmp_path), home=_nohome(tmp_path))
        assert "STYLE RULE" in block and "TESTS RULE" in block
        assert block.index("STYLE RULE") < block.index("TESTS RULE")

    def test_non_md_files_ignored(self, tmp_path):
        _w(str(tmp_path / ".korgex" / "rules" / "notes.txt"), "IGNORE ME")
        _w(str(tmp_path / ".korgex" / "rules" / "real.md"), "KEEP ME")
        block = PR.load_project_rules(str(tmp_path), home=_nohome(tmp_path))
        assert "KEEP ME" in block and "IGNORE ME" not in block


# ── the directory hierarchy (the real point) ─────────────────────────────────

class TestHierarchy:
    def test_monorepo_root_and_package_both_merge(self, tmp_path):
        # tmp_path is the git root with house rules; we launch inside pkg/.
        _w(str(tmp_path / ".git" / "HEAD"), "ref: refs/heads/main")
        _w(str(tmp_path / "AGENTS.md"), "MONOREPO RULE")
        _w(str(tmp_path / "pkg" / "AGENTS.md"), "PACKAGE RULE")
        block = PR.load_project_rules(str(tmp_path / "pkg"), home=_nohome(tmp_path))
        assert "MONOREPO RULE" in block and "PACKAGE RULE" in block
        # least-specific (monorepo root) first, most-specific (package) last
        assert block.index("MONOREPO RULE") < block.index("PACKAGE RULE")

    def test_never_walks_above_the_git_root(self, tmp_path):
        # An AGENTS.md ABOVE the git root must never be read.
        _w(str(tmp_path / "AGENTS.md"), "ABOVE THE REPO")
        _w(str(tmp_path / "repo" / ".git" / "HEAD"), "ref: refs/heads/main")
        _w(str(tmp_path / "repo" / "AGENTS.md"), "INSIDE THE REPO")
        block = PR.load_project_rules(str(tmp_path / "repo"), home=_nohome(tmp_path))
        assert "INSIDE THE REPO" in block
        assert "ABOVE THE REPO" not in block


# ── user-global rules ────────────────────────────────────────────────────────

class TestUserGlobal:
    def test_home_rules_come_first(self, tmp_path):
        home = str(tmp_path / "home")
        _w(os.path.join(home, ".korgex", "AGENTS.md"), "GLOBAL RULE")
        repo = str(tmp_path / "repo")
        _w(os.path.join(repo, "AGENTS.md"), "REPO RULE")
        block = PR.load_project_rules(repo, home=home)
        assert "GLOBAL RULE" in block and "REPO RULE" in block
        assert block.index("GLOBAL RULE") < block.index("REPO RULE")
