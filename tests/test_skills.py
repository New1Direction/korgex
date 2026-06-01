"""Skills system — file-defined, progressively-disclosed reusable workflows.

A skill is a directory with a SKILL.md (YAML frontmatter: name/description/version,
optional trust tier) + an instruction body. The loader indexes them, injects a
compact index into the prompt (name + one-line description only — the body is
loaded on demand when the skill is invoked). Provenance/trust tiers gate what may
be auto-curated later. Pure parse/index/resolve here; the Skill tool routes to it.
"""
from src import skills as S


def _write_skill(root, name, description, body="do the thing", version="1.0", trust="user"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: {version}\ntrust: {trust}\n---\n\n{body}\n"
    )
    return d


# ── parsing a single SKILL.md ──────────────────────────────────────────────────

def test_parse_skill_frontmatter(tmp_path):
    p = _write_skill(tmp_path, "fix-flaky", "find and fix flaky tests", body="STEP 1...")
    skill = S.parse_skill(str(p / "SKILL.md"))
    assert skill.name == "fix-flaky"
    assert skill.description == "find and fix flaky tests"
    assert skill.version == "1.0"
    assert skill.trust == "user"
    assert "STEP 1" in skill.body


def test_parse_skill_missing_frontmatter_returns_none(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("just a plain markdown file, no frontmatter\n")
    assert S.parse_skill(str(f)) is None


def test_parse_skill_defaults_trust_when_absent(tmp_path):
    d = tmp_path / "s"; d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody\n")
    skill = S.parse_skill(str(d / "SKILL.md"))
    assert skill.trust == "user"  # default tier when unspecified


# ── loading a directory of skills ──────────────────────────────────────────────

def test_load_skills_finds_all(tmp_path):
    _write_skill(tmp_path, "alpha", "the alpha skill")
    _write_skill(tmp_path, "beta", "the beta skill")
    reg = S.load_skills([str(tmp_path)])
    assert set(reg.names()) == {"alpha", "beta"}


def test_load_skills_empty_when_no_dir(tmp_path):
    reg = S.load_skills([str(tmp_path / "does-not-exist")])
    assert reg.names() == []


def test_get_skill_returns_body(tmp_path):
    _write_skill(tmp_path, "deploy", "ship it", body="1. build\n2. push")
    reg = S.load_skills([str(tmp_path)])
    sk = reg.get("deploy")
    assert sk and "2. push" in sk.body


def test_get_unknown_skill_is_none(tmp_path):
    reg = S.load_skills([str(tmp_path)])
    assert reg.get("nope") is None


# ── the compact prompt index (name + description only; body withheld) ──────────

def test_index_block_lists_name_and_description_only(tmp_path):
    _write_skill(tmp_path, "fix-flaky", "find and fix flaky tests", body="SECRET BODY")
    reg = S.load_skills([str(tmp_path)])
    block = reg.index_block()
    assert "fix-flaky" in block and "find and fix flaky tests" in block
    assert "SECRET BODY" not in block  # body is loaded on demand, not in the index


def test_index_block_empty_when_no_skills(tmp_path):
    reg = S.load_skills([str(tmp_path / "nope")])
    assert reg.index_block() == ""


# ── invoking a skill returns its body (progressive disclosure) ─────────────────

def test_invoke_known_skill_returns_body(tmp_path):
    _write_skill(tmp_path, "review", "review a PR", body="checklist: ...")
    reg = S.load_skills([str(tmp_path)])
    res = S.invoke_skill(reg, "review", args="PR#12")
    assert res["ok"] is True
    assert "checklist" in res["body"]
    assert res["name"] == "review"


def test_invoke_unknown_skill_errors_without_guessing(tmp_path):
    reg = S.load_skills([str(tmp_path)])
    res = S.invoke_skill(reg, "made-up", args="")
    assert res["ok"] is False
    assert "made-up" in res["error"]


# ── integration: prompt index + Skill tool routing ────────────────────────────

def test_skill_index_injected_into_system_prompt(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    sdir = tmp_path / ".korgex" / "skills"
    _write_skill(sdir, "ship-it", "deploy to production safely", body="DO NOT SHOW THIS BODY")
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    sp = a._assemble_system_prompt()
    assert "ship-it" in sp and "deploy to production safely" in sp
    assert "DO NOT SHOW THIS BODY" not in sp  # body withheld from the index


def test_skill_tool_routes_to_body(tmp_path):
    import src.tool_abstraction as TA
    sdir = tmp_path / ".korgex" / "skills"
    _write_skill(sdir, "review", "review a PR", body="checklist line")
    out = TA.route_tool_call("Skill", {"skill": "review"}, repo_root=str(tmp_path))
    assert out["ok"] is True and "checklist line" in out["body"]


def test_skill_tool_unknown_errors(tmp_path):
    import src.tool_abstraction as TA
    out = TA.route_tool_call("Skill", {"skill": "ghost"}, repo_root=str(tmp_path))
    assert out["ok"] is False and "ghost" in out["error"]
