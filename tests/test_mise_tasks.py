"""mise project-task auto-discovery (src/mise_tasks.py).

When a repo uses [mise](https://github.com/jdx/mise), it declares the real build/
test/lint commands as mise tasks. Surfacing them in the agent's context means korgex
runs the project's actual commands (`mise run test`) instead of guessing. The
subprocess (`mise tasks ls --json`) is injected so this is unit-tested offline.
"""
import json

from src import mise_tasks as MT


def test_detect_finds_mise_config(tmp_path):
    assert MT.detect(str(tmp_path)) is False
    (tmp_path / "mise.toml").write_text("[tasks.build]\nrun = 'echo hi'\n")
    assert MT.detect(str(tmp_path)) is True


def test_detect_dot_mise_toml(tmp_path):
    (tmp_path / ".mise.toml").write_text("")
    assert MT.detect(str(tmp_path)) is True


def test_list_tasks_parses_json_array():
    payload = json.dumps([
        {"name": "build", "description": "compile"},
        {"name": "test", "description": ""},
    ])
    tasks = MT.list_tasks("/repo", run=lambda cmd, cwd: payload)
    assert tasks == [{"name": "build", "description": "compile"},
                     {"name": "test", "description": ""}]


def _boom(cmd, cwd):
    raise OSError("mise not installed")


def test_list_tasks_tolerates_garbage_and_missing_mise():
    assert MT.list_tasks("/r", run=lambda c, w: "not json") == []
    assert MT.list_tasks("/r", run=_boom) == []                 # runner raises → []
    assert MT.list_tasks("/r", run=lambda c, w: json.dumps({"nope": 1})) == []


def test_render_block_lists_tasks_or_empty():
    assert MT.render_block([]) == ""
    block = MT.render_block([{"name": "test", "description": "run the tests"},
                             {"name": "lint", "description": ""}])
    assert "mise run test" in block and "run the tests" in block
    assert "mise run lint" in block


def test_project_task_block_detect_then_list(tmp_path):
    (tmp_path / "mise.toml").write_text("[tasks.lint]\nrun = 'ruff check'\n")
    block = MT.project_task_block(
        str(tmp_path), run=lambda c, w: json.dumps([{"name": "lint", "description": "lint it"}]))
    assert "mise run lint" in block
    # no mise config → empty, and the runner is never consulted
    assert MT.project_task_block(str(tmp_path / "absent"), run=_boom) == ""


def test_agent_appends_the_mise_block_to_the_system_prompt(tmp_path):
    # The wiring: a computed mise block (cached on the agent) lands in the prompt.
    from src.agent import KorgexAgent
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    a._mise_block = "# Project tasks (mise)\n- `mise run test` — run tests"
    sp = a._assemble_system_prompt()
    assert "mise run test" in sp


def test_agent_no_mise_block_when_absent(tmp_path):
    from src.agent import KorgexAgent
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)  # tmp repo has no mise.toml
    sp = a._assemble_system_prompt()
    assert "Project tasks (mise)" not in sp
