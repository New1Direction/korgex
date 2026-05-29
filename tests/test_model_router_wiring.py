"""
model_router wiring tests (roadmap #5 cleanup).

The README advertised per-mode generation params (plan → 64k tokens + 20k thinking
budget, etc.) but the loop hardcoded max_tokens=4096 and never sent thinking or
temperature — MODE_PARAMS was dead code. This wires it: the agent resolves params
from its mode and _call sends them. (Thinking and temperature are mutually
exclusive on Anthropic, so temperature is omitted when a thinking budget is set.)
"""

import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.agent import KorgexAgent  # noqa: E402


def test_plan_mode_sends_thinking_budget_no_temperature():
    a = KorgexAgent(mode="plan")           # → opus / anthropic / MODE_PARAMS["plan"]
    assert a.provider == "anthropic"
    kw = a._gen_kwargs()
    assert kw["max_tokens"] == 64000
    assert kw["thinking"] == {"type": "enabled", "budget_tokens": 20000}
    assert "temperature" not in kw         # omitted under extended thinking


def test_execute_mode_sends_temperature_no_thinking():
    a = KorgexAgent(mode="execute")        # → sonnet / no thinking
    kw = a._gen_kwargs()
    assert kw["max_tokens"] == 64000
    assert kw["temperature"] == 0.3
    assert "thinking" not in kw


def test_default_no_mode_preserves_4096():
    a = KorgexAgent(model="gpt-4o")        # no mode → unchanged default
    kw = a._gen_kwargs()
    assert kw["max_tokens"] == 4096
    assert "thinking" not in kw


def test_openai_never_gets_thinking_even_with_mode():
    a = KorgexAgent(model="gpt-4o", mode="plan")  # mode set but provider is openai
    kw = a._gen_kwargs()
    assert "thinking" not in kw            # thinking is Anthropic-only
    assert kw["max_tokens"] == 64000


def test_call_forwards_gen_kwargs_to_the_api():
    captured = {}

    class _FakeOpenAI:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(usage=None, choices=[
                        SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])

    a = KorgexAgent(model="gpt-4o", mode="execute", interactive=False)
    a._call(_FakeOpenAI(), messages=[], tools=[])
    assert captured["max_tokens"] == 64000
    assert captured["temperature"] == 0.3
