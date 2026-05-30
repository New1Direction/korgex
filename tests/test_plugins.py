"""In-process plugin registry — the lean delta over korgex's command-hooks.

korgex already shells out to command-hooks (src/hooks.py) on UserPromptSubmit/
PreToolUse/PostToolUse/Stop. This adds a complementary IN-PROCESS lifecycle:
register Python callables (lower latency, richer access) — generalizing the
`witness` tap into a registerable observer surface. Fail-safe: one bad plugin
can never break the agent loop.
"""
from __future__ import annotations

import pytest

from src.plugins import VALID_HOOKS, PluginRegistry


def test_register_and_invoke_runs_callables_in_order():
    reg = PluginRegistry()
    seen = []
    reg.register("pre_tool", lambda call: seen.append(("a", call["name"])))
    reg.register("pre_tool", lambda call: seen.append(("b", call["name"])))
    reg.invoke("pre_tool", {"name": "Write"})
    assert seen == [("a", "Write"), ("b", "Write")]


def test_invoke_collects_non_none_results_and_skips_none():
    reg = PluginRegistry()
    reg.register("post_tool", lambda r: None)
    reg.register("post_tool", lambda r: {"observed": r})
    out = reg.invoke("post_tool", "x")
    assert out == [{"observed": "x"}]


def test_registering_an_unknown_hook_is_rejected():
    reg = PluginRegistry()
    with pytest.raises(ValueError):
        reg.register("not_a_hook", lambda: None)


def test_a_failing_plugin_is_isolated_and_never_breaks_the_loop():
    reg = PluginRegistry()
    calls = []

    def boom(_):
        raise RuntimeError("plugin crashed")

    reg.register("post_tool", boom)
    reg.register("post_tool", lambda r: calls.append(r))
    # invoke must NOT raise, and the healthy plugin still runs
    reg.invoke("post_tool", "payload")
    assert calls == ["payload"]


def test_empty_registry_is_a_zero_overhead_noop():
    reg = PluginRegistry()
    assert reg.invoke("pre_tool", {"name": "Read"}) == []
    assert reg.count() == 0


def test_count_reports_registered_plugins():
    reg = PluginRegistry()
    reg.register("pre_tool", lambda c: None)
    reg.register("post_tool", lambda r: None)
    assert reg.count() == 2
    assert reg.count("pre_tool") == 1


def test_valid_hooks_cover_the_tool_lifecycle():
    assert {"on_user_prompt", "pre_tool", "post_tool", "on_stop"} <= VALID_HOOKS
