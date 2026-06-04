"""Resolution helpers extracted from the agent loop into src/agent_resolve.py — now
independently testable in isolation (the point of pulling them out of the 2,700-line core)."""
from src import agent_resolve as R


def test_looks_anthropic():
    assert R._looks_anthropic("claude-sonnet-4-6") is True
    assert R._looks_anthropic("anthropic/claude-3") is True
    assert R._looks_anthropic("gpt-4o") is False
    assert R._looks_anthropic("") is False


def test_subagent_tools_readonly_vs_full():
    assert R.subagent_tools("explore") == ["Read", "Grep", "Glob", "Recall"]   # read-only subset
    full = R.subagent_tools("code")
    assert "Read" in full
    assert "Agent" not in full and "Orchestrate" not in full    # a subagent can't recurse / fan out


def test_resolve_params_defaults_without_a_mode():
    assert R._resolve_params("")["max_tokens"] == 4096


def test_oauth_provider_for_grok_else_none():
    assert R._oauth_provider_for("grok-4") == "grok"            # substring → BYO-OAuth provider
    assert R._oauth_provider_for("gpt-4o") is None             # not a BYO-OAuth provider
    assert R._oauth_provider_for("") is None


def test_oauth_base_urls_are_https_endpoints():
    assert "grok" in R._OAUTH_BASE_URLS
    assert R._OAUTH_BASE_URLS["grok"].startswith("https://")
