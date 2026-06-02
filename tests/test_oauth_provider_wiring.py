"""Wire BYO-OAuth providers into the LIVE agent loop.

Before this, GrokClient/GeminiClient/ClaudeClient (model_router) were unreachable
from `korgex "task"`: _get_client only ever returned an OpenAI/Anthropic SDK client
keyed from config, so `--model grok4` fell through to the OpenAI path with a config
key — never the user's ~/.grok OAuth. This wires it: _get_client recognizes a BYO
provider, mints a bearer token from the existing loader, and points the SDK at the
provider's endpoint — but ONLY when no api-key is configured (so existing api-key
users are untouched). self.provider / _call() are unchanged.
"""
import openai

import src.agent as A
import src.config as C


class _FakeOpenAI:
    def __init__(self, api_key=None, base_url=None, **kw):
        self.api_key = api_key
        self.base_url = base_url


def _agent(model):
    return A.KorgexAgent(model=model, load_mcp=False, interactive=False)


# ── model → BYO-OAuth provider ──────────────────────────────────────────

def test_oauth_provider_for_grok():
    assert A._oauth_provider_for("grok4") == "grok"
    assert A._oauth_provider_for("grok-420-reasoning") == "grok"


def test_oauth_provider_for_gemini():
    assert A._oauth_provider_for("gemini-flash") == "gemini"
    assert A._oauth_provider_for("gemini-2.5-flash") == "gemini"


def test_oauth_provider_for_claude_is_none():
    # Claude stays on its api-key path: the Claude Code OAuth token is rejected by
    # the raw Anthropic API, so Claude is intentionally NOT a BYO-OAuth provider.
    assert A._oauth_provider_for("opus48") is None
    assert A._oauth_provider_for("claude-opus-4-8") is None


def test_oauth_provider_for_none_on_plain_openai():
    assert A._oauth_provider_for("gpt-4o") is None
    assert A._oauth_provider_for("") is None


# ── alias key → concrete model id ───────────────────────────────────────

def test_resolve_model_maps_alias_to_model_id():
    assert A._resolve_model("gemini-flash", None) == "gemini-2.5-flash"
    assert A._resolve_model("opus48", None) == "claude-opus-4-8"


def test_resolve_model_passes_through_concrete_ids():
    assert A._resolve_model("gpt-4o", None) == "gpt-4o"
    assert A._resolve_model("claude-sonnet-4-6", None) == "claude-sonnet-4-6"


# ── token loader reuse ──────────────────────────────────────────────────

def test_oauth_token_and_base_grok(monkeypatch):
    import src.model_router as MR
    monkeypatch.setattr(MR.GrokClient, "_load_token", lambda self: None)
    monkeypatch.setattr(MR.GrokClient, "_ensure_token", lambda self: "grok-tok")
    tok, base = A._oauth_token_and_base("grok")
    assert tok == "grok-tok"
    assert base == "https://api.x.ai/v1"


def test_oauth_token_and_base_none_when_no_credential(monkeypatch):
    import src.model_router as MR
    monkeypatch.setattr(MR.GrokClient, "_load_token", lambda self: None)

    def _boom(self):
        raise RuntimeError("no token")

    monkeypatch.setattr(MR.GrokClient, "_ensure_token", _boom)
    tok, base = A._oauth_token_and_base("grok")
    assert tok is None
    assert base == "https://api.x.ai/v1"   # endpoint still known


# ── _get_client dispatch ────────────────────────────────────────────────

def test_get_client_uses_grok_oauth_when_no_api_key(monkeypatch):
    monkeypatch.setattr(C, "load_config", lambda: object())
    monkeypatch.setattr(C, "resolve_client_config", lambda *a, **k: (None, None))
    monkeypatch.setattr(A, "_oauth_token_and_base",
                        lambda p: ("grok-tok", "https://api.x.ai/v1"))
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    c = _agent("grok4")._get_client()
    assert isinstance(c, _FakeOpenAI)
    assert c.api_key == "grok-tok"
    assert c.base_url == "https://api.x.ai/v1"


def test_get_client_uses_gemini_oauth_when_no_api_key(monkeypatch):
    monkeypatch.setattr(C, "load_config", lambda: object())
    monkeypatch.setattr(C, "resolve_client_config", lambda *a, **k: (None, None))
    monkeypatch.setattr(
        A, "_oauth_token_and_base",
        lambda p: ("gem-tok", "https://generativelanguage.googleapis.com/v1beta/openai/"))
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    c = _agent("gemini-flash")._get_client()
    assert isinstance(c, _FakeOpenAI)
    assert c.api_key == "gem-tok"
    assert c.base_url.endswith("/v1beta/openai/")


def test_get_client_prefers_configured_api_key_over_oauth(monkeypatch):
    # Regression: a configured api-key must win — OAuth never hijacks it.
    monkeypatch.setattr(C, "load_config", lambda: object())
    monkeypatch.setattr(C, "resolve_client_config",
                        lambda *a, **k: ("real-key", "https://api.openai.com/v1"))
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    consulted = {"oauth": False}

    def _spy(p):
        consulted["oauth"] = True
        return ("x", "https://api.x.ai/v1")

    monkeypatch.setattr(A, "_oauth_token_and_base", _spy)
    c = _agent("grok4")._get_client()       # grok IS a BYO-OAuth provider…
    assert c.api_key == "real-key"          # …but the configured key still wins
    assert consulted["oauth"] is False


def test_nous_prefix_strips_and_forces_openai_transport(monkeypatch):
    # `nous/<vendor/model>` routes through the Nous subscription: prefix stripped
    # for the API, OpenAI-compatible transport, OAuth forced.
    a = _agent("nous/anthropic/claude-opus-4.8")
    assert a.model == "anthropic/claude-opus-4.8"
    assert a.provider == "openai"
    assert a._oauth_force == "nous"


def test_oauth_token_and_base_nous(monkeypatch):
    import src.model_router as MR
    monkeypatch.setattr(MR.NousClient, "_load_auth", lambda self: None)
    monkeypatch.setattr(MR.NousClient, "_ensure_key", lambda self: "sk-nous-x")
    tok, base = A._oauth_token_and_base("nous")
    assert tok == "sk-nous-x"
    assert base == "https://inference-api.nousresearch.com/v1"


def test_get_client_nous_prefix_uses_oauth_even_with_api_key(monkeypatch):
    # The explicit nous/ prefix always routes to Nous, even if a key is configured.
    monkeypatch.setattr(C, "load_config", lambda: object())
    monkeypatch.setattr(C, "resolve_client_config", lambda *a, **k: ("some-key", None))
    monkeypatch.setattr(
        A, "_oauth_token_and_base",
        lambda p: ("sk-nous-x", "https://inference-api.nousresearch.com/v1"))
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    c = _agent("nous/qwen/qwen3.7-max")._get_client()
    assert isinstance(c, _FakeOpenAI)
    assert c.api_key == "sk-nous-x"
    assert c.base_url == "https://inference-api.nousresearch.com/v1"


def test_venice_prefix_strips_and_forces_openai_transport(monkeypatch):
    a = _agent("venice/venice-uncensored")
    assert a.model == "venice-uncensored"
    assert a.provider == "openai"
    assert a._oauth_force == "venice"


def test_oauth_token_and_base_venice_reads_env(monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "vk-123")
    tok, base = A._oauth_token_and_base("venice")
    assert tok == "vk-123"
    assert base == "https://api.venice.ai/api/v1"


def test_get_client_venice_prefix_uses_venice_key(monkeypatch):
    monkeypatch.setattr(C, "load_config", lambda: object())
    monkeypatch.setattr(C, "resolve_client_config", lambda *a, **k: (None, None))
    monkeypatch.setattr(
        A, "_oauth_token_and_base", lambda p: ("vk-123", "https://api.venice.ai/api/v1"))
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    c = _agent("venice/llama-3.3-70b")._get_client()
    assert c.api_key == "vk-123"
    assert c.base_url == "https://api.venice.ai/api/v1"


def test_get_client_openai_path_unchanged_for_plain_model(monkeypatch):
    # Regression: a non-OAuth model is unaffected by the new branch.
    monkeypatch.setattr(C, "load_config", lambda: object())
    monkeypatch.setattr(C, "resolve_client_config",
                        lambda *a, **k: ("k", "https://api.openai.com/v1"))
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    c = _agent("gpt-4o")._get_client()
    assert c.api_key == "k"
    assert c.base_url == "https://api.openai.com/v1"
