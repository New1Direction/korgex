"""korgex setup wizard — pure core: turn a list of answered provider prompts into
a Config, and suggest a sensible default model per provider. The getpass/input
shell around this stays thin."""
from src import setup_wizard as W
from src import config as C


def test_build_config_from_answers_cloud_provider():
    answers = [
        {"type": "openrouter", "api_key": "sk-or-1"},
        {"type": "anthropic", "api_key": "sk-ant-2"},
    ]
    cfg = W.build_config(answers, default_model="claude-opus-4-8")
    assert isinstance(cfg, C.Config)
    assert cfg.default_model == "claude-opus-4-8"
    assert cfg.api_key_for("openrouter") == "sk-or-1"
    assert cfg.api_key_for("anthropic") == "sk-ant-2"


def test_build_config_local_provider_has_base_url_no_key():
    answers = [{"type": "ollama", "base_url": "http://localhost:11434"}]
    cfg = W.build_config(answers, default_model="llama3.3")
    p = cfg.provider_for("ollama")
    assert p["base_url"] == "http://localhost:11434"
    assert "api_key" not in p


def test_default_model_suggestion_per_provider():
    assert W.suggest_default_model("anthropic").startswith("claude")
    assert "gpt" in W.suggest_default_model("openai")
    assert "/" in W.suggest_default_model("openrouter")  # vendor/model form
    assert W.suggest_default_model("ollama")  # non-empty


def test_known_provider_types():
    # The wizard offers these; free-text model ids still allowed downstream.
    assert set(["anthropic", "openai", "openrouter", "ollama"]).issubset(set(W.PROVIDER_TYPES))


def test_build_config_empty_answers_is_unconfigured():
    cfg = W.build_config([], default_model=None)
    assert cfg.is_configured() is False
