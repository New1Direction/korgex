"""Tests for `korgex init` scaffolding (src/project_init.py).

`korgex init` bootstraps a project's AGENTS.md — the context file the project-rules
hierarchy then reads every session. It detects the stack and test/build commands so
the file starts useful, and never clobbers an existing AGENTS.md. Detection and
rendering are pure (filesystem in, strings out), so they test cleanly.
"""
import os

from src import project_init as PI


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("")


class TestDetectStack:
    def test_python_via_pyproject(self, tmp_path):
        _touch(str(tmp_path / "pyproject.toml"))
        facts = PI.detect_stack(str(tmp_path))
        assert "Python" in facts["languages"]
        assert facts["test_cmd"] == "pytest -q"

    def test_node_via_package_json(self, tmp_path):
        _touch(str(tmp_path / "package.json"))
        facts = PI.detect_stack(str(tmp_path))
        assert "JavaScript/TypeScript" in facts["languages"]
        assert facts["test_cmd"] == "npm test"

    def test_rust_via_cargo(self, tmp_path):
        _touch(str(tmp_path / "Cargo.toml"))
        facts = PI.detect_stack(str(tmp_path))
        assert "Rust" in facts["languages"]
        assert facts["test_cmd"] == "cargo test"

    def test_go_via_go_mod(self, tmp_path):
        _touch(str(tmp_path / "go.mod"))
        facts = PI.detect_stack(str(tmp_path))
        assert "Go" in facts["languages"]

    def test_polyglot_lists_all_and_picks_first_test_cmd(self, tmp_path):
        _touch(str(tmp_path / "pyproject.toml"))
        _touch(str(tmp_path / "package.json"))
        facts = PI.detect_stack(str(tmp_path))
        assert "Python" in facts["languages"] and "JavaScript/TypeScript" in facts["languages"]
        assert facts["test_cmd"] == "pytest -q"     # python detected first

    def test_unknown_stack_is_graceful(self, tmp_path):
        facts = PI.detect_stack(str(tmp_path))
        assert facts["languages"] == []
        assert facts["test_cmd"] is None


class TestRenderAgentsMd:
    def test_includes_name_headings_and_commands(self, tmp_path):
        facts = {"languages": ["Python"], "manifests": ["pyproject.toml"],
                 "test_cmd": "pytest -q", "build_cmd": None}
        md = PI.render_agents_md(facts, project_name="myproj")
        assert md.startswith("# myproj")
        assert "## Commands" in md and "pytest -q" in md
        assert "## Conventions" in md            # placeholder section to fill in
        assert "Python" in md

    def test_unknown_stack_renders_todo_commands(self, tmp_path):
        facts = {"languages": [], "manifests": [], "test_cmd": None, "build_cmd": None}
        md = PI.render_agents_md(facts, project_name="x")
        assert "TODO" in md                       # no detected command → a clear TODO


class TestScaffold:
    def test_writes_when_absent_and_refuses_to_clobber(self, tmp_path):
        d = str(tmp_path)
        res = PI.scaffold(d)
        assert res["written"] is True
        assert os.path.isfile(os.path.join(d, "AGENTS.md"))
        # second run must NOT overwrite
        with open(os.path.join(d, "AGENTS.md")) as f:
            before = f.read()
        res2 = PI.scaffold(d)
        assert res2["written"] is False
        with open(os.path.join(d, "AGENTS.md")) as f:
            assert f.read() == before             # untouched
