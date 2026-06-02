"""Tests for the cross-vendor prompt-caching layer (src/prompt_cache.py).

Prompt caching keeps the expensive, STABLE prefix (system prompt + tool defs) in
the provider's cache so repeated turns skip reprocessing it — faster first token,
cheaper. The universal rule across every provider: the cached prefix must be
byte-identical turn to turn, so VOLATILE per-turn content (the live task list)
must be kept OUT of the cached system prompt.

The hard part is that providers disagree on HOW to cache:
  • OpenAI / Gemini / Grok / DeepSeek cache AUTOMATICALLY (≥1024 tok) — no marker.
  • Anthropic Claude / Alibaba Qwen need a MANUAL ``cache_control`` breakpoint.

These helpers are the single source of truth for "what shape does THIS provider
need", so the agent's call path stays clean and every branch is covered here.
"""
import pytest

from src import prompt_cache as PC
from src.agent import KorgexAgent


# ── provider/model detection ────────────────────────────────────────────────

class TestDetection:
    def test_is_openrouter_true_for_openrouter_base_url(self):
        assert PC.is_openrouter("https://openrouter.ai/api/v1") is True

    def test_is_openrouter_false_for_openai(self):
        assert PC.is_openrouter("https://api.openai.com/v1") is False

    def test_is_openrouter_false_for_none(self):
        assert PC.is_openrouter(None) is False

    def test_manual_breakpoint_for_claude(self):
        assert PC.needs_manual_breakpoint("anthropic/claude-3.5-sonnet") is True

    def test_manual_breakpoint_for_qwen(self):
        assert PC.needs_manual_breakpoint("qwen/qwen-2.5-72b-instruct") is True

    def test_no_manual_breakpoint_for_gpt(self):
        # gpt-4o caches automatically on OpenRouter — a marker is unneeded.
        assert PC.needs_manual_breakpoint("openai/gpt-4o") is False

    def test_no_manual_breakpoint_for_gemini_or_grok(self):
        assert PC.needs_manual_breakpoint("google/gemini-2.5-pro") is False
        assert PC.needs_manual_breakpoint("x-ai/grok-2") is False

    def test_no_manual_breakpoint_for_none(self):
        assert PC.needs_manual_breakpoint(None) is False


# ── the single "do we add explicit markers?" decision ───────────────────────

class TestShouldMark:
    OR = "https://openrouter.ai/api/v1"
    OA = "https://api.openai.com/v1"

    def test_mark_openrouter_claude(self):
        # OpenRouter routing to a manual-breakpoint model → yes, mark it.
        assert PC.should_mark("openai", self.OR, "anthropic/claude-3.5-sonnet") is True

    def test_no_mark_openrouter_gpt(self):
        # gpt auto-caches; sending markers is pointless.
        assert PC.should_mark("openai", self.OR, "openai/gpt-4o") is False

    def test_no_mark_pure_openai(self):
        # api.openai.com rejects the unknown cache_control field — never mark.
        assert PC.should_mark("openai", self.OA, "gpt-4o") is False

    def test_no_mark_for_anthropic_sdk_path(self):
        # The native Anthropic SDK path uses anthropic_system()/with_tool_cache(),
        # NOT the OpenAI-compatible marker path — so should_mark is False there.
        assert PC.should_mark("anthropic", None, "claude-3.5-sonnet") is False


# ── Anthropic native: system blocks + tools ──────────────────────────────────

class TestAnthropicSystem:
    def test_stable_only_gets_one_cached_block(self):
        blocks = PC.anthropic_system("SYSTEM PROMPT")
        assert blocks == [
            {"type": "text", "text": "SYSTEM PROMPT",
             "cache_control": {"type": "ephemeral"}},
        ]

    def test_volatile_trails_as_separate_uncached_block(self):
        # The whole point: the task list changes every turn and must NOT carry the
        # breakpoint, or it would invalidate the cached prefix each time.
        blocks = PC.anthropic_system("STABLE", "TASK LIST")
        assert len(blocks) == 2
        assert blocks[0]["text"] == "STABLE"
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert blocks[1] == {"type": "text", "text": "TASK LIST"}
        assert "cache_control" not in blocks[1]

    def test_empty_volatile_is_omitted(self):
        assert len(PC.anthropic_system("STABLE", "")) == 1
        assert len(PC.anthropic_system("STABLE", None)) == 1


class TestToolCache:
    def test_last_tool_carries_the_breakpoint(self):
        tools = [{"name": "a"}, {"name": "b"}]
        out = PC.with_tool_cache(tools)
        assert out[-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in out[0]
        assert out[-1]["name"] == "b"  # other fields preserved

    def test_does_not_mutate_input(self):
        tools = [{"name": "a"}, {"name": "b"}]
        PC.with_tool_cache(tools)
        assert "cache_control" not in tools[-1]

    def test_empty_and_none_pass_through(self):
        assert PC.with_tool_cache([]) == []
        assert PC.with_tool_cache(None) is None


# ── OpenAI-compatible (OpenRouter) request shaping ───────────────────────────

class TestOpenAISystemMessage:
    def test_plain_string_when_not_caching(self):
        # Auto-cache providers (and api.openai.com) want a plain string — sending
        # cache_control parts would be useless or rejected.
        msg = PC.openai_system_message("SYS", cache=False)
        assert msg == {"role": "system", "content": "SYS"}

    def test_parts_with_breakpoint_when_caching(self):
        msg = PC.openai_system_message("SYS", cache=True)
        assert msg == {
            "role": "system",
            "content": [
                {"type": "text", "text": "SYS",
                 "cache_control": {"type": "ephemeral"}},
            ],
        }


class TestOpenAICacheExtra:
    OR = "https://openrouter.ai/api/v1"
    OA = "https://api.openai.com/v1"

    def test_top_level_breakpoint_for_openrouter_manual_model(self):
        # OpenRouter's top-level cache_control auto-advances the breakpoint across
        # the growing conversation — caches HISTORY too, not just the system prompt.
        assert PC.openai_cache_extra("openai", self.OR, "anthropic/claude-3.5-sonnet") == {
            "cache_control": {"type": "ephemeral"}
        }

    def test_no_extra_for_auto_cache_model(self):
        assert PC.openai_cache_extra("openai", self.OR, "openai/gpt-4o") == {}

    def test_no_extra_for_pure_openai(self):
        assert PC.openai_cache_extra("openai", self.OA, "gpt-4o") == {}


class TestOpenAITaskReminder:
    def test_volatile_becomes_a_trailing_system_message(self):
        # The task list rides as a trailing message — kept after the cached prefix
        # so it steers the model without invalidating the cache.
        assert PC.openai_task_reminder("DO X") == {"role": "system", "content": "DO X"}

    def test_empty_volatile_is_none(self):
        assert PC.openai_task_reminder("") is None
        assert PC.openai_task_reminder(None) is None


# ── agent wiring: the cache layer actually reaches the provider call ─────────

class TestAgentWiring:
    OR = "https://openrouter.ai/api/v1"

    def test_anthropic_kwargs_cache_system_and_tools(self, tmp_path):
        a = KorgexAgent(repo_root=str(tmp_path), model="claude-3.5-sonnet", interactive=False)
        kw = a._anthropic_cache_kwargs("SYS", [{"name": "a"}, {"name": "b"}], "TASKS")
        # system: stable block cached, volatile task block trailing + uncached
        assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert kw["system"][1] == {"type": "text", "text": "TASKS"}
        # tools: the whole array cached via a breakpoint on the last one
        assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in kw["tools"][0]

    def test_openai_kwargs_mark_when_routing_claude(self, tmp_path):
        a = KorgexAgent(repo_root=str(tmp_path), model="anthropic/claude-3.5-sonnet",
                        interactive=False)
        a.provider = "openai"          # OpenRouter speaks the OpenAI dialect
        a._base_url = self.OR
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
        kw = a._openai_cache_kwargs(msgs, [{"name": "t"}])
        # the stable system message carries a manual breakpoint (Claude needs one)
        assert kw["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        # and the top-level breakpoint that auto-advances over history
        assert kw["extra_body"] == {"cache_control": {"type": "ephemeral"}}

    def test_openai_kwargs_plain_for_gpt(self, tmp_path):
        a = KorgexAgent(repo_root=str(tmp_path), model="openai/gpt-4o", interactive=False)
        a.provider = "openai"
        a._base_url = self.OR
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
        kw = a._openai_cache_kwargs(msgs, [{"name": "t"}])
        # gpt-4o auto-caches → leave it a plain string, add no extra fields
        assert kw["messages"][0] == {"role": "system", "content": "SYS"}
        assert "extra_body" not in kw

    def test_openai_kwargs_appends_task_list_as_trailing_message(self, tmp_path):
        # The known gap: on gpt-4o the task list never reached the model. It must
        # ride as a TRAILING message (after the cached prefix) — present on the wire,
        # but not baked into the stable system message that auto-caches.
        a = KorgexAgent(repo_root=str(tmp_path), model="openai/gpt-4o", interactive=False)
        a.provider = "openai"
        a._base_url = self.OR
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
        kw = a._openai_cache_kwargs(msgs, [{"name": "t"}], volatile="ZZTASK do it")
        assert kw["messages"][-1] == {"role": "system", "content": "ZZTASK do it"}
        assert kw["messages"][0] == {"role": "system", "content": "SYS"}  # prefix unchanged
        assert len(msgs) == 2  # the caller's history is never mutated


# ── the core invariant: volatile content stays OUT of the cached prefix ──────

class _FakeLedger:
    def record_user_prompt(self, prompt, triggered_by=None):
        return 1

    def record_llm_call(self, **kw):
        return 2

    def record_tool_call(self, **kw):
        return None


class _Stop(Exception):
    """Sentinel: stop the agent loop right after the first _call so we can
    inspect exactly what the cache layer was handed."""


class _CapturingAgent(KorgexAgent):
    def __init__(self, **kw):
        kw.setdefault("model", "gpt-4o")
        kw.setdefault("interactive", False)
        super().__init__(**kw)
        self.ledger = _FakeLedger()
        self.captured = None

    def _get_client(self):
        return object()

    def _call(self, client, messages, tools, output_schema=None,
              system_prompt=None, system_volatile=None):
        self.captured = {"system_prompt": system_prompt, "system_volatile": system_volatile}
        raise _Stop()


def test_task_list_is_volatile_not_baked_into_cached_system_prompt(tmp_path):
    # An open task must reach the model, but as VOLATILE content — never folded
    # into the stable system prompt, or it would bust the cached prefix every
    # time a task's status changes. This is what makes the caching actually stick.
    agent = _CapturingAgent(repo_root=str(tmp_path))
    agent._task_ledger.set_tasks(["ZZMARKER wire the cache layer"])
    with pytest.raises(_Stop):
        agent.run_task("go")
    assert "ZZMARKER" in agent.captured["system_volatile"]
    assert "ZZMARKER" not in (agent.captured["system_prompt"] or "")

