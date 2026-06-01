"""Tests for the self-learning skill CURATOR (src/skill_curator.py).

korgex learns skills on its own (skill_review.py writes trust:agent SKILL.md files
after useful turns). Left alone, that library accumulates near-duplicates — three
slightly-different "run the test suite" skills, etc. The curator is the consolidation
pass: an LLM groups the agent-learned skills by intent, we merge each group into one
skill and delete the redundant ones.

The non-negotiable invariant — proven hard here — is PROVENANCE: the curator only ever
reads-for-merge, rewrites, or deletes ``trust: agent`` skills. User / built-in /
installed skills are sacred: never merged away, never clobbered, never deleted. The
LLM is behind an injected callable so the whole thing tests offline.
"""
import os

from src import skill_curator as C
from src.skill_review import write_agent_skill
from src.skills import Skill, SkillRegistry, load_skills


def _agent(name, desc="d", body="b"):
    return Skill(name=name, description=desc, body=body, trust="agent",
                 path=f"/x/{name}/SKILL.md")


def _user(name, desc="d", body="b"):
    return Skill(name=name, description=desc, body=body, trust="user",
                 path=f"/x/{name}/SKILL.md")


# ── selecting the curator's scope ────────────────────────────────────────────

class TestAgentSkills:
    def test_filters_to_agent_trust_only(self):
        reg = SkillRegistry([
            _agent("run-tests"), _user("deploy"),
            Skill(name="lint", description="d", body="b", trust="built-in"),
            _agent("execute-tests"),
        ])
        got = sorted(s.name for s in C.agent_skills(reg))
        assert got == ["execute-tests", "run-tests"]

    def test_empty_when_no_agent_skills(self):
        reg = SkillRegistry([_user("deploy")])
        assert C.agent_skills(reg) == []


# ── prompt + reply parsing ───────────────────────────────────────────────────

class TestPromptAndParse:
    def test_prompt_lists_each_agent_skill(self):
        reg = SkillRegistry([_agent("run-tests", "run the suite"),
                             _agent("exec-tests", "execute tests")])
        p = C.build_curation_prompt(C.agent_skills(reg))
        assert "run-tests" in p and "exec-tests" in p

    def test_parse_extracts_json_array(self):
        txt = '[{"into":"run-tests","merge":["run-tests","exec-tests"],"body":"x"}]'
        out = C.parse_curation_reply(txt)
        assert out[0]["into"] == "run-tests"

    def test_parse_tolerates_prose_and_fences(self):
        txt = "Sure!\n```json\n[{\"into\":\"a\",\"merge\":[\"a\",\"b\"],\"body\":\"x\"}]\n```\n"
        assert C.parse_curation_reply(txt)[0]["into"] == "a"

    def test_parse_garbage_is_empty_list(self):
        assert C.parse_curation_reply("no json here") == []
        assert C.parse_curation_reply("") == []


# ── planning (validation lives here) ─────────────────────────────────────────

class TestPlanCuration:
    def _curator(self, groups):
        return lambda ctx: groups

    def test_merges_two_agent_skills(self):
        reg = SkillRegistry([_agent("run-tests"), _agent("exec-tests")])
        plan = C.plan_curation(reg, self._curator(
            [{"into": "run-tests", "merge": ["run-tests", "exec-tests"],
              "description": "run the suite", "body": "steps", "reason": "dupes"}]))
        assert len(plan.groups) == 1
        g = plan.groups[0]
        assert g.into == "run-tests"
        assert sorted(g.members) == ["exec-tests", "run-tests"]
        assert g.body == "steps"

    def test_drops_group_touching_a_non_agent_skill(self):
        # 'deploy' is a USER skill — it must be filtered out of the merge members,
        # leaving <2 agent members, so the whole group is dropped (never merge a
        # user skill away).
        reg = SkillRegistry([_agent("run-tests"), _user("deploy")])
        plan = C.plan_curation(reg, self._curator(
            [{"into": "run-tests", "merge": ["run-tests", "deploy"], "body": "x"}]))
        assert plan.groups == []

    def test_drops_single_member_group(self):
        reg = SkillRegistry([_agent("run-tests"), _agent("exec-tests")])
        plan = C.plan_curation(reg, self._curator(
            [{"into": "run-tests", "merge": ["run-tests"], "body": "x"}]))
        assert plan.groups == []

    def test_drops_group_with_no_body(self):
        reg = SkillRegistry([_agent("a"), _agent("b")])
        plan = C.plan_curation(reg, self._curator(
            [{"into": "a", "merge": ["a", "b"]}]))
        assert plan.groups == []

    def test_malformed_curator_yields_empty_plan(self):
        reg = SkillRegistry([_agent("a"), _agent("b")])

        def boom(ctx):
            raise RuntimeError("model down")
        assert C.plan_curation(reg, boom).groups == []
        assert C.plan_curation(reg, lambda ctx: "not a list").groups == []


# ── applying (filesystem + provenance guard) ─────────────────────────────────

class TestApplyCuration:
    def _seed(self, skills_dir, names):
        for n in names:
            write_agent_skill(skills_dir, n, f"{n} desc", f"{n} body")
        return load_skills([skills_dir])

    def test_writes_merged_and_deletes_redundant(self, tmp_path):
        d = str(tmp_path)
        reg = self._seed(d, ["run-tests", "exec-tests"])
        plan = C.CurationPlan(groups=[C.CurationGroup(
            into="run-tests", members=["run-tests", "exec-tests"],
            description="run the suite", body="MERGED BODY", reason="dupes")])
        res = C.apply_curation(plan, d, reg)
        # consolidated skill written with the merged body
        merged = open(os.path.join(d, "run-tests", "SKILL.md")).read()
        assert "MERGED BODY" in merged and "trust: agent" in merged
        # the redundant source dir is gone
        assert not os.path.isdir(os.path.join(d, "exec-tests"))
        assert res["merged"] == ["run-tests"]
        assert res["removed"] == ["exec-tests"]

    def test_never_deletes_a_non_agent_skill(self, tmp_path):
        # Defense-in-depth: even if a group names a user skill, apply must not rm it.
        d = str(tmp_path)
        reg = self._seed(d, ["run-tests"])
        # add a USER skill on disk + in the registry
        user_dir = os.path.join(d, "deploy")
        os.makedirs(user_dir, exist_ok=True)
        with open(os.path.join(user_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: deploy\ndescription: d\ntrust: user\n---\nbody\n")
        reg = load_skills([d])
        plan = C.CurationPlan(groups=[C.CurationGroup(
            into="run-tests", members=["run-tests", "deploy"],
            description="x", body="b", reason="r")])
        C.apply_curation(plan, d, reg)
        assert os.path.isdir(user_dir)  # user skill untouched

    def test_refuses_to_clobber_a_non_agent_into_name(self, tmp_path):
        d = str(tmp_path)
        # a user skill named 'build' exists; a group tries to write 'build'
        user_dir = os.path.join(d, "build")
        os.makedirs(user_dir, exist_ok=True)
        with open(os.path.join(user_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: build\ndescription: USER\ntrust: user\n---\nuser body\n")
        reg = load_skills([d])
        plan = C.CurationPlan(groups=[C.CurationGroup(
            into="build", members=["x", "y"], description="d", body="AGENT BODY", reason="r")])
        res = C.apply_curation(plan, d, reg)
        # the user skill's file is NOT overwritten
        assert "user body" in open(os.path.join(user_dir, "SKILL.md")).read()
        assert "build" in res["skipped"]


def test_make_curator_wraps_a_complete_callable():
    seen = {}

    def complete(system, user):
        seen["system"], seen["user"] = system, user
        return '[{"into":"a","merge":["a","b"],"body":"x"}]'

    curator = C.make_curator(complete)
    out = curator({"skills": [{"name": "a", "description": "d"},
                              {"name": "b", "description": "d"}]})
    assert out[0]["into"] == "a"
    assert "a" in seen["user"]  # the skill list reached the prompt
