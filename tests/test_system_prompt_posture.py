"""The agent's baseline posture: free to act, and aware it can reach the web.

These pin two behavioral requirements (not prose): after opening up privilege +
tool use, the agent must (1) know it has WebSearch/WebFetch so it stops claiming
it can't browse, and (2) be framed to act freely rather than ask permission for
routine work.
"""
from src.agent import SYSTEM_PROMPT


def test_prompt_tells_the_agent_it_can_reach_the_web():
    sp = SYSTEM_PROMPT.lower()
    assert "websearch" in sp and "webfetch" in sp


def test_prompt_frames_free_decisive_action():
    sp = SYSTEM_PROMPT.lower()
    assert "free" in sp
    # and it should NOT be told the user gets prompted for permission on routine work
    assert "prompted for permission" not in sp


def test_prompt_still_flags_untrusted_tool_content():
    # We just gave it web reach — it must treat fetched content as data, not commands.
    sp = SYSTEM_PROMPT.lower()
    assert "untrusted" in sp or "injection" in sp or "not follow instructions" in sp


# ── synthesized frontier-agent posture (the "best of each", recreated) ───────

def test_prompt_demands_output_economy_on_both_ends():
    # The hallmark of the best agents: no preamble AND no postamble. A trivial ask
    # gets a trivial answer with no trailing recap.
    sp = SYSTEM_PROMPT.lower()
    assert "concise" in sp or "terse" in sp
    assert "postamble" in sp or "no summary" in sp or "don't summarize" in sp


def test_prompt_sets_a_direct_non_sycophantic_tone():
    sp = SYSTEM_PROMPT.lower()
    assert "sycophan" in sp or "flattery" in sp or "push back" in sp


def test_prompt_guards_code_quality_and_conventions():
    sp = SYSTEM_PROMPT.lower()
    assert "convention" in sp or "surrounding" in sp or "existing" in sp
    assert "comment" in sp                       # comments discipline (no noise)


def test_prompt_warns_against_surprising_out_of_scope_actions():
    # Decisive on the task, but doesn't go wandering — the proactiveness balance.
    sp = SYSTEM_PROMPT.lower()
    assert "scope" in sp or "surprise" in sp or "out-of-scope" in sp
