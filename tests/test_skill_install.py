"""Installing skills from the open Agent-Skills catalog (src/skill_install.py).

korgex's skills are the Anthropic Agent-Skills format (a dir with a SKILL.md), the
same format skills.sh / vercel-labs / anthropics publish on GitHub — so the whole
public catalog is consumable with no adapter. These tests pin the install pipeline:
resolve a ref (local path / git URL / `owner/repo[@skill]`), find the SKILL.md dirs,
and install them as `trust: installed` with a `source:` provenance stamp. Network
(git clone, the skills.sh HTTP search) is injected, so everything runs offline.
"""
import json
import os

from src import skill_install as SI
from src.skills import load_skills, _parse_frontmatter


# ── ref resolution ──────────────────────────────────────────────────────────────

def test_resolve_ref_local_markers(tmp_path):
    for ref in ("./foo", "../foo", "/abs/foo", "~/foo"):
        assert SI.resolve_ref(ref)[0] == "local"
    # an existing path with no prefix is still local
    d = tmp_path / "skilldir"
    d.mkdir()
    assert SI.resolve_ref(str(d))[0] == "local"


def test_resolve_ref_git_urls():
    for ref in ("https://github.com/o/r.git", "git@github.com:o/r.git",
                "https://example.com/o/r"):
        assert SI.resolve_ref(ref)[0] == "git"


def test_resolve_ref_registry_shorthand():
    assert SI.resolve_ref("owner/repo")[0] == "registry"
    assert SI.resolve_ref("vercel-labs/agent-skills@react")[0] == "registry"


def test_resolve_ref_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        SI.resolve_ref("not a ref!! spaces")


def test_registry_to_git():
    assert SI.registry_to_git("owner/repo@skill") == ("https://github.com/owner/repo.git", "skill")
    assert SI.registry_to_git("owner/repo") == ("https://github.com/owner/repo.git", None)


# ── finding SKILL.md dirs (uppercase + legacy lowercase) ────────────────────────

def _make_skill(dir_path, name, marker="SKILL.md", desc="does a thing"):
    os.makedirs(dir_path, exist_ok=True)
    with open(os.path.join(dir_path, marker), "w") as f:
        f.write(f"---\nname: {name}\ndescription: {desc}\n---\n\nBody of {name}.\n")


def test_find_skill_dirs_accepts_upper_and_lower_marker(tmp_path):
    _make_skill(tmp_path / "a", "alpha", "SKILL.md")
    _make_skill(tmp_path / "b", "beta", "skill.md")     # legacy lowercase
    (tmp_path / "c").mkdir()                              # not a skill
    found = {os.path.basename(p) for p in SI.find_skill_dirs(str(tmp_path))}
    assert found == {"a", "b"}


# ── serialize round-trips with the parser ───────────────────────────────────────

def test_serialize_skill_round_trips():
    text = SI.serialize_skill({"name": "x", "description": "d", "trust": "installed"}, "Body here.")
    meta, body = _parse_frontmatter(text)
    assert meta["name"] == "x" and meta["trust"] == "installed"
    assert body == "Body here."


# ── install from a local dir → trust:installed + source stamp ───────────────────

def test_install_local_marks_installed_and_records_source(tmp_path):
    src = tmp_path / "src" / "myskill"
    _make_skill(src, "myskill")
    dest = tmp_path / "store"
    names = SI.install(str(tmp_path / "src"), str(dest), source_label="local:src")
    assert "myskill" in names
    # it landed as an installed skill the loader can see
    reg = load_skills([str(dest)])
    sk = reg.get("myskill")
    assert sk is not None and sk.trust == "installed"
    # provenance is stamped into the installed SKILL.md
    installed_text = open(os.path.join(str(dest), "myskill", "SKILL.md")).read()
    meta, _ = _parse_frontmatter(installed_text)
    assert meta.get("source") == "local:src"


def test_adopt_imports_every_skill_in_a_dir(tmp_path):
    _make_skill(tmp_path / "claudeskills" / "one", "one")
    _make_skill(tmp_path / "claudeskills" / "two", "two", marker="skill.md")
    dest = tmp_path / "store"
    names = set(SI.adopt(str(tmp_path / "claudeskills"), str(dest)))
    assert names == {"one", "two"}
    reg = load_skills([str(dest)])
    assert reg.get("one").trust == "installed" and reg.get("two").trust == "installed"


# ── install from git / registry (clone injected) ────────────────────────────────

def test_install_git_uses_injected_clone(tmp_path):
    # the "remote" is just a local dir the fake clone returns
    remote = tmp_path / "checkout"
    _make_skill(remote / "gitskill", "gitskill")
    called = {}

    def fake_clone(url, subpath):
        called["url"] = url
        called["subpath"] = subpath
        return str(remote)

    dest = tmp_path / "store"
    names = SI.install("owner/repo", str(dest), clone=fake_clone)
    assert "gitskill" in names
    assert called["url"] == "https://github.com/owner/repo.git"
    reg = load_skills([str(dest)])
    sk = reg.get("gitskill")
    assert sk.trust == "installed"
    meta, _ = _parse_frontmatter(open(os.path.join(str(dest), "gitskill", "SKILL.md")).read())
    assert "github.com/owner/repo" in (meta.get("source") or "")


# ── skills.sh search (HTTP injected) ────────────────────────────────────────────

def test_search_parses_skillssh_results():
    payload = {"results": [
        {"source": "vercel-labs/agent-skills", "skillId": "react", "name": "React", "installs": 12000},
        {"source": "anthropics/skills", "skillId": "pdf", "name": "PDF", "installs": 800},
    ]}

    def fake_get(url):
        assert "skills.sh" in url and "react" in url
        return json.dumps(payload)

    out = SI.search("react", http_get=fake_get, limit=10)
    assert len(out) == 2
    assert out[0]["source"] == "vercel-labs/agent-skills"
    assert out[0]["installs"] == 12000


def test_search_tolerates_garbage_response():
    assert SI.search("x", http_get=lambda url: "not json") == []
    assert SI.search("x", http_get=lambda url: json.dumps({"nope": 1})) == []


# ── export: push korgex skills out to other agents (.claude / .cursor / …) ───────

def test_resolve_export_target_known_agents_and_paths(tmp_path):
    proj = str(tmp_path)
    assert SI.resolve_export_target("claude", proj) == os.path.join(proj, ".claude", "skills")
    assert SI.resolve_export_target("cursor", proj) == os.path.join(proj, ".cursor", "skills")
    assert SI.resolve_export_target("codex", proj) == os.path.join(proj, ".codex", "skills")
    # an unknown target is treated as a literal directory (with ~ expansion)
    assert SI.resolve_export_target("/tmp/x", proj) == "/tmp/x"
    assert SI.resolve_export_target("~/y", proj) == os.path.expanduser("~/y")


def test_export_skill_copies_dir(tmp_path):
    src = tmp_path / "src" / "myskill"
    _make_skill(src, "myskill")
    target = tmp_path / "out"
    dest = SI.export_skill(str(src), str(target), "myskill")
    assert os.path.isfile(os.path.join(dest, "SKILL.md"))
    assert dest == os.path.join(str(target), "myskill")


def test_export_skills_by_name_skips_unknown(tmp_path):
    # build a korgex store with two skills, load it, export them by name
    store = tmp_path / "store"
    _make_skill(store / "one", "one")
    _make_skill(store / "two", "two")
    reg = load_skills([str(store)])
    target = tmp_path / "out"
    done = SI.export_skills(["one", "two", "ghost"], str(target), reg)
    names = {n for n, _ in done}
    assert names == {"one", "two"}                       # ghost (unknown) skipped
    assert os.path.isfile(os.path.join(str(target), "one", "SKILL.md"))
    assert os.path.isfile(os.path.join(str(target), "two", "SKILL.md"))
