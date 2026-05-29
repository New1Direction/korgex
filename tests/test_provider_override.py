"""
Provider override — route any model through any transport.

The agent auto-detects provider from the model id (claude* / anthropic/* →
Anthropic SDK, else OpenAI-compatible). That breaks when you drive a Claude or
Gemini model through an OpenAI-compatible gateway (OpenRouter, LiteLLM, a proxy):
`anthropic/claude-opus-4.7` looks Anthropic, so the loop would hit the native
Anthropic API instead of the gateway. KORGEX_PROVIDER forces the transport,
overriding autodetect, so e.g. Claude-via-OpenRouter works.
"""

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.agent import KorgexAgent  # noqa: E402


def test_anthropic_named_model_forced_to_openai_transport(monkeypatch):
    monkeypatch.setenv("KORGEX_PROVIDER", "openai")
    a = KorgexAgent(model="anthropic/claude-opus-4.7", interactive=False)
    assert a.provider == "openai"      # so it rides the OpenRouter/OpenAI path


def test_override_can_force_anthropic(monkeypatch):
    monkeypatch.setenv("KORGEX_PROVIDER", "anthropic")
    a = KorgexAgent(model="gpt-4o", interactive=False)
    assert a.provider == "anthropic"


def test_garbage_override_falls_back_to_autodetect(monkeypatch):
    monkeypatch.setenv("KORGEX_PROVIDER", "banana")
    a = KorgexAgent(model="anthropic/claude-sonnet-4.6", interactive=False)
    assert a.provider == "anthropic"   # ignored → autodetect


def test_no_override_keeps_autodetect(monkeypatch):
    monkeypatch.delenv("KORGEX_PROVIDER", raising=False)
    assert KorgexAgent(model="anthropic/claude-sonnet-4.6", interactive=False).provider == "anthropic"
    assert KorgexAgent(model="google/gemini-3.5-flash", interactive=False).provider == "openai"
