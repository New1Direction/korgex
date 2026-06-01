"""Self-learning: turn a completed turn into a saved skill.

`review_turn` asks an injected reviewer (the LLM, faked here) whether the turn
produced a reusable, generalizable skill; `apply_verdict` writes/updates an
AGENT-created skill — and refuses to clobber a user/built-in skill of the same name.
"""
from src.skill_review import SkillVerdict, apply_verdict, review_turn, write_agent_skill
from src.skills import Skill, SkillRegistry, parse_skill


def test_review_turn_returns_create_verdict():
    v = review_turn("fix the flaky test", "added a retry wrapper", [], reviewer=lambda ctx: {
        "action": "create", "name": "retry-flaky", "description": "retry flaky tests",
        "body": "Wrap the call in a 3x retry", "reason": "reusable"})
    assert v.action == "create" and v.name == "retry-flaky" and "retry" in v.body.lower()


def test_review_turn_none_when_reviewer_declines_or_malformed():
    assert review_turn("hi", "hello", [], reviewer=lambda ctx: {"action": "none"}).action == "none"
    assert review_turn("hi", "hello", [], reviewer=lambda ctx: {}).action == "none"
    assert review_turn("hi", "hello", [], reviewer=lambda ctx: None).action == "none"


def test_review_turn_passes_context_to_reviewer():
    seen = {}

    def rev(ctx):
        seen.update(ctx)
        return {"action": "none"}
    review_turn("U", "S", ["a", "b"], reviewer=rev)
    assert seen["user"] == "U" and seen["summary"] == "S" and seen["existing"] == ["a", "b"]


def test_apply_verdict_writes_an_agent_skill(tmp_path):
    v = SkillVerdict("create", name="My Skill", description="does a thing", body="step 1\nstep 2")
    res = apply_verdict(v, str(tmp_path))
    assert res["saved"] is True
    sk = parse_skill(res["path"])
    assert sk.name == "My Skill" and sk.trust == "agent" and "step 1" in sk.body


def test_apply_verdict_refuses_to_clobber_a_user_skill(tmp_path):
    reg = SkillRegistry([Skill(name="precious", description="d", body="b", trust="user")])
    res = apply_verdict(SkillVerdict("create", name="precious", description="x", body="y"),
                        str(tmp_path), registry=reg)
    assert res["saved"] is False and "user" in res["reason"]


def test_apply_verdict_noop_for_none_action(tmp_path):
    assert apply_verdict(SkillVerdict("none"), str(tmp_path))["saved"] is False


def test_apply_verdict_updates_existing_agent_skill(tmp_path):
    write_agent_skill(str(tmp_path), "learn", "v1 desc", "v1 body")
    reg = SkillRegistry([Skill(name="learn", description="v1 desc", body="v1 body", trust="agent")])
    res = apply_verdict(SkillVerdict("update", name="learn", description="v2 desc", body="v2 body"),
                        str(tmp_path), registry=reg)
    assert res["saved"] is True
    assert "v2 body" in parse_skill(res["path"]).body


# ── the default (LLM) reviewer, behind an injected `complete(system,user)->str` ──

def test_parse_review_reply_extracts_json_from_prose():
    from src.skill_review import parse_review_reply
    text = 'Sure!\n```json\n{"action":"create","name":"x","body":"y"}\n```\nhope that helps'
    got = parse_review_reply(text)
    assert got["action"] == "create" and got["name"] == "x"


def test_parse_review_reply_garbage_is_empty():
    from src.skill_review import parse_review_reply
    assert parse_review_reply("no json here") == {}
    assert parse_review_reply("") == {}


def test_make_reviewer_wires_complete_into_a_verdict():
    from src.skill_review import make_reviewer
    captured = {}

    def fake_complete(system, user):
        captured["system"] = system
        captured["user"] = user
        return '{"action":"create","name":"retry","description":"d","body":"steps"}'

    reviewer = make_reviewer(fake_complete)
    v = review_turn("fix flaky", "added retry", ["other"], reviewer=reviewer)
    assert v.action == "create" and v.name == "retry"
    assert "reusable" in captured["system"].lower() or "skill" in captured["system"].lower()
    assert "fix flaky" in captured["user"]
