"""Regression: `korgex "task"` must honor the configured default model.

Found by dogfooding: a user who ran `korgex setup` and picked openai/gpt-4o got a
401 (invalid x-api-key) from `korgex "..."`, because _resolve_model fell straight
to a hardcoded claude-sonnet default and never consulted config.default_model — so
it used the wrong provider/key. The REPL worked only because it resolved the model
separately. This pins the precedence: explicit → mode → config default → env →
builtin (matching config.resolve_model_and_key).
"""
from types import SimpleNamespace

from src import agent as A
from src import config as C


def test_uses_config_default_when_no_explicit_model_or_mode(monkeypatch):
    monkeypatch.setattr(C, "load_config", lambda: SimpleNamespace(default_model="openai/gpt-4o"))
    monkeypatch.delenv("KORGEX_MODEL", raising=False)
    assert A._resolve_model(None, None) == "openai/gpt-4o"


def test_explicit_model_still_wins_over_config(monkeypatch):
    monkeypatch.setattr(C, "load_config", lambda: SimpleNamespace(default_model="openai/gpt-4o"))
    assert A._resolve_model("anthropic/claude-opus-4-8", None) == "anthropic/claude-opus-4-8"


def test_env_used_when_config_has_no_default(monkeypatch):
    monkeypatch.setattr(C, "load_config", lambda: SimpleNamespace(default_model=None))
    monkeypatch.setenv("KORGEX_MODEL", "x-ai/grok-2")
    assert A._resolve_model(None, None) == "x-ai/grok-2"


def test_builtin_default_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(C, "load_config", lambda: SimpleNamespace(default_model=None))
    monkeypatch.delenv("KORGEX_MODEL", raising=False)
    assert A._resolve_model(None, None) == "claude-sonnet-4-6"


def test_config_read_failure_falls_back_safely(monkeypatch):
    def boom():
        raise RuntimeError("no config")
    monkeypatch.setattr(C, "load_config", boom)
    monkeypatch.delenv("KORGEX_MODEL", raising=False)
    assert A._resolve_model(None, None) == "claude-sonnet-4-6"   # never crashes
