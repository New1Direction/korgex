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
