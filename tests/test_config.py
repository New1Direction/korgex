"""korgex CLI config layer: ~/.korgex/config.json — provider keys + default model.

JSON (not TOML) because the target runs Python 3.9 (no stdlib tomllib) and we keep
korgex zero-dep to avoid clean-install breakage.
"""
import os
import stat

from src import config as C


def test_missing_file_is_empty_config(tmp_path):
    cfg = C.load_config(str(tmp_path / "nope.json"))
    assert cfg.providers == []
    assert cfg.default_model is None
    assert cfg.is_configured() is False


def test_save_then_load_roundtrips(tmp_path):
    path = str(tmp_path / "config.json")
    cfg = C.Config(
        default_model="claude-opus-4-8",
        providers=[
            {"type": "openrouter", "api_key": "sk-or-xyz"},
            {"type": "ollama", "base_url": "http://localhost:11434"},
        ],
    )
    C.save_config(cfg, path)
    back = C.load_config(path)
    assert back.default_model == "claude-opus-4-8"
    assert back.api_key_for("openrouter") == "sk-or-xyz"
    assert back.provider_for("ollama")["base_url"] == "http://localhost:11434"
    assert back.is_configured() is True


def test_saved_file_is_chmod_600(tmp_path):
    path = str(tmp_path / "config.json")
    C.save_config(C.Config(default_model="m", providers=[{"type": "anthropic", "api_key": "k"}]), path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"config holds secret keys; must be 0o600, got {oct(mode)}"


def test_resolve_precedence_explicit_arg_wins(tmp_path):
    cfg = C.Config(default_model="config-model", providers=[{"type": "anthropic", "api_key": "k"}])
    model, _key = C.resolve_model_and_key("explicit-model", cfg, env={"KORGEX_MODEL": "env-model"})
    assert model == "explicit-model"


def test_resolve_precedence_config_default_over_env(tmp_path):
    cfg = C.Config(default_model="config-model", providers=[])
    model, _key = C.resolve_model_and_key(None, cfg, env={"KORGEX_MODEL": "env-model"})
    assert model == "config-model"


def test_resolve_falls_back_to_env_then_builtin(tmp_path):
    empty = C.Config(default_model=None, providers=[])
    model, _ = C.resolve_model_and_key(None, empty, env={"KORGEX_MODEL": "env-model"})
    assert model == "env-model"
    model2, _ = C.resolve_model_and_key(None, empty, env={})
    assert model2  # some built-in default, never empty


def test_resolve_maps_model_to_its_provider_key(tmp_path):
    cfg = C.Config(
        default_model="claude-opus-4-8",
        providers=[{"type": "anthropic", "api_key": "sk-ant-CONFIG"}],
    )
    _model, key = C.resolve_model_and_key("claude-opus-4-8", cfg, env={})
    assert key == "sk-ant-CONFIG"


def test_resolve_key_env_fallback_keeps_existing_users_working(tmp_path):
    # No provider saved, but ANTHROPIC_API_KEY in env → still resolves a key.
    empty = C.Config(default_model=None, providers=[])
    _model, key = C.resolve_model_and_key("claude-opus-4-8", empty, env={"ANTHROPIC_API_KEY": "sk-ant-ENV"})
    assert key == "sk-ant-ENV"


# ── client config resolution (key + base_url for the active model's provider) ──

def test_resolve_client_openrouter_uses_config_key_and_or_url():
    cfg = C.Config(default_model="openai/gpt-4o",
                   providers=[{"type": "openrouter", "api_key": "sk-or-CONFIG"}])
    key, base_url = C.resolve_client_config("openai/gpt-4o", cfg, env={})
    assert key == "sk-or-CONFIG"
    assert base_url and "openrouter.ai" in base_url  # routed to OpenRouter, not api.openai.com


def test_resolve_client_anthropic_has_no_base_url_override():
    cfg = C.Config(default_model="claude-sonnet-4-6",
                   providers=[{"type": "anthropic", "api_key": "sk-ant-X"}])
    key, base_url = C.resolve_client_config("claude-sonnet-4-6", cfg, env={})
    assert key == "sk-ant-X"
    assert base_url is None  # anthropic SDK uses its own default endpoint


def test_resolve_client_ollama_local_base_url_no_key():
    cfg = C.Config(default_model="llama3.3",
                   providers=[{"type": "ollama", "base_url": "http://localhost:11434/v1"}])
    key, base_url = C.resolve_client_config("llama3.3", cfg, env={})
    assert "11434" in base_url
    # local needs no key — a placeholder is fine, but it must not be None-crash
    assert key is not None


# ── custom self-hosted OpenAI-compatible endpoint (KORGEX_API_URL) ────────────

def test_custom_endpoint_routes_unknown_model_to_it():
    # A self-hosted vLLM model name shouldn't fall back to Anthropic — KORGEX_API_URL
    # is an explicit "use my OpenAI-compatible endpoint".
    cfg = C.Config(default_model="Qwen2.5-Coder-32B", providers=[])
    key, base_url = C.resolve_client_config(
        "Qwen2.5-Coder-32B", cfg, env={"KORGEX_API_URL": "http://vast-box:8000/v1"})
    assert base_url == "http://vast-box:8000/v1"
    assert key  # a keyless vLLM still needs a non-empty placeholder so the client builds


def test_custom_endpoint_uses_real_key_when_present():
    cfg = C.Config(default_model="my-model", providers=[])
    key, base_url = C.resolve_client_config(
        "my-model", cfg, env={"KORGEX_API_URL": "http://host:8000/v1", "OPENAI_API_KEY": "sk-real"})
    assert base_url == "http://host:8000/v1"
    assert key == "sk-real"


def test_custom_endpoint_does_not_hijack_claude():
    cfg = C.Config(default_model="claude-sonnet-4-6", providers=[])
    key, base_url = C.resolve_client_config(
        "claude-sonnet-4-6", cfg, env={"KORGEX_API_URL": "http://host:8000/v1"})
    assert base_url is None      # claude still goes to Anthropic even with KORGEX_API_URL set


def test_resolve_client_env_key_fallback():
    # no config provider, but OPENAI_API_KEY in env → still resolves
    key, base_url = C.resolve_client_config("gpt-4o", C.Config(),
                                            env={"OPENAI_API_KEY": "sk-ENV"})
    assert key == "sk-ENV"
