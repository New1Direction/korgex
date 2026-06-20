"""Custom slash commands — markdown-defined, user/project/built-in (Claude-Code-style).

A command is a `.md` file with optional frontmatter (`description`, `argument-hint`); the
body, with `$ARGUMENTS`/`$1..$9` substituted, becomes the turn prompt. Roots layer
built-in → project (.korgex/commands) → user (~/.korgex/commands).
"""
from __future__ import annotations

import os

from src import commands as C


def _write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def test_parse_command_with_frontmatter(tmp_path):
    p = str(tmp_path / "cmds" / "code-review.md")
    _write(p, "---\ndescription: Review code\nargument-hint: [pr | blank]\n---\n# Review\nReview $ARGUMENTS now.")
    cmd = C.parse_command(p)
    assert cmd.name == "code-review"           # name comes from the filename
    assert cmd.description == "Review code"
    assert cmd.argument_hint == "[pr | blank]"
    assert "Review $ARGUMENTS now." in cmd.body
    assert "---" not in cmd.body               # frontmatter stripped


def test_parse_command_without_frontmatter(tmp_path):
    p = str(tmp_path / "cmds" / "hello.md")
    _write(p, "Just do the thing with $ARGUMENTS.")
    cmd = C.parse_command(p)
    assert cmd.name == "hello"
    assert cmd.description == ""
    assert "Just do the thing" in cmd.body


def test_load_commands_precedence(tmp_path):
    builtin, proj = tmp_path / "builtin", tmp_path / "proj"
    _write(str(builtin / "x.md"), "---\ndescription: builtin x\n---\nBUILTIN")
    _write(str(proj / "x.md"), "---\ndescription: project x\n---\nPROJECT")
    _write(str(builtin / "y.md"), "---\ndescription: y\n---\nYBODY")
    reg = C.load_commands([str(builtin), str(proj)])
    assert set(reg.names()) == {"x", "y"}
    assert "PROJECT" in reg.get("x").body              # a later root shadows an earlier one
    assert reg.get("x").description == "project x"


def test_render_command_substitutes_arguments(tmp_path):
    p = str(tmp_path / "c" / "g.md")
    _write(p, "Open PR $1 and review $ARGUMENTS.")
    cmd = C.parse_command(p)
    assert C.render_command(cmd, "42 --fast") == "Open PR 42 and review 42 --fast."


def test_default_command_roots_are_layered(tmp_path):
    roots = C.default_command_roots(repo_root=str(tmp_path))
    assert roots[0] == C.builtin_commands_root()
    assert any(os.path.join(".korgex", "commands") in r for r in roots)
    assert len(roots) >= 3


def test_builtin_commands_load_and_are_well_formed():
    """The commands korgex ships must all parse + carry a description + a body."""
    reg = C.load_commands([C.builtin_commands_root()])
    assert len(reg.names()) >= 1
    for name in reg.names():
        cmd = reg.get(name)
        assert cmd.description, f"built-in command {name!r} is missing a description"
        assert cmd.body, f"built-in command {name!r} has an empty body"


def test_builtin_commands_are_in_package_data():
    """Built-in commands must survive wheel/sdist packaging, not only source checkouts."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    pyproject = open(os.path.join(root, "pyproject.toml"), encoding="utf-8").read()
    assert "commands_builtin/*.md" in pyproject


# ── REPL + CLI wiring ─────────────────────────────────────────────────────────

def test_repl_runs_custom_command(tmp_path, monkeypatch):
    import io

    from src import repl as REPL
    _write(str(tmp_path / ".korgex" / "commands" / "ship.md"),
           "---\ndescription: ship it\n---\nShip $ARGUMENTS to prod.")
    r = REPL.Repl(out=io.StringIO())
    r.repo_root = str(tmp_path)
    captured = {}
    monkeypatch.setattr(r, "_run_turn", lambda text: captured.update(text=text))
    cmd = REPL.parse_repl_input("/ship the API")
    assert cmd.kind == "custom"
    r.handle(cmd)
    assert captured.get("text") == "Ship the API to prod."


def test_repl_unknown_command_falls_back_to_hint(tmp_path, monkeypatch):
    import io

    from src import repl as REPL
    out = io.StringIO()
    r = REPL.Repl(out=out)
    r.repo_root = str(tmp_path)                     # empty → no custom commands
    called = {}
    monkeypatch.setattr(r, "_run_turn", lambda text: called.update(text=text))
    r.handle(REPL.parse_repl_input("/definitelynotacommand"))
    assert "text" not in called                     # did NOT run a turn
    assert "unknown command" in out.getvalue()


def test_cmd_commands_lists_builtin_commands(capsys):
    from src import cli
    rc = cli.cmd_commands()
    out = capsys.readouterr().out
    assert rc == 0
    assert "/code-review" in out and "/build-fix" in out
