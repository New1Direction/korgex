"""korgex CLI configuration — connect any model provider, no lock-in.

Stores a list of providers (one OpenRouter key reaches hundreds of models; or
direct Anthropic/OpenAI; or local Ollama with no key) plus a default model, in
``~/.korgex/config.json`` (override with ``$KORGEX_CONFIG``). JSON, not TOML:
the target runs Python 3.9 (no stdlib ``tomllib``) and korgex stays zero-dep to
avoid clean-install breakage. The file holds secret keys, so it is written 0o600.

The schema is a *list* of providers so "support every provider" is just more list
entries — adding one never reshapes the file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# Which env var holds the key for each provider, for back-compat fallback so
# users who already `export ANTHROPIC_API_KEY=…` keep working with no config.
_ENV_KEY = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# Maps a model id to its most likely provider type, so a chosen model resolves to
# the right saved key. Heuristic + overridable by an explicit provider entry.
def provider_type_for_model(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("claude") or "anthropic" in m:
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4")) or "openai" in m:
        return "openai"
    if m.startswith("ollama/"):  # local Ollama tag — must beat the generic "/" rule
        return "ollama"
    if "/" in m:  # vendor/model form is the OpenRouter convention
        return "openrouter"
    return "anthropic"

_BUILTIN_DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass
class Config:
    """In-memory view of ~/.korgex/config.json."""
    default_model: str | None = None
    providers: list[dict] = field(default_factory=list)
    active_provider: str | None = None

    def provider_for(self, ptype: str) -> dict | None:
        """The first saved provider entry of this type, or None."""
        for p in self.providers:
            if p.get("type") == ptype:
                return p
        return None

    def provider_by_name(self, name: str | None) -> dict | None:
        """The saved provider entry with this name, or None."""
        for p in self.providers:
            if name and p.get("name") == name:
                return p
        return None

    def active(self) -> dict | None:
        """The selected named provider (set by ``korgex providers use``), or None."""
        return self.provider_by_name(self.active_provider) if self.active_provider else None

    def api_key_for(self, ptype: str) -> str | None:
        p = self.provider_for(ptype)
        return p.get("api_key") if p else None

    def is_configured(self) -> bool:
        """True once at least one provider (any type) is connected."""
        return bool(self.providers)

    def to_dict(self) -> dict:
        return {"default_model": self.default_model, "providers": self.providers,
                "active_provider": self.active_provider}


def default_path() -> str:
    """The config path: ``$KORGEX_CONFIG`` if set, else ``~/.korgex/config.json``."""
    env = os.environ.get("KORGEX_CONFIG")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".korgex", "config.json")


def load_config(path: str | None = None) -> Config:
    """Load config. A missing or unreadable/corrupt file yields an empty Config
    (never raises) so a fresh machine just looks 'not configured'."""
    path = path or default_path()
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return Config()
    providers = data.get("providers") or []
    if not isinstance(providers, list):
        providers = []
    return Config(default_model=data.get("default_model"), providers=providers,
                  active_provider=data.get("active_provider"))


def save_config(cfg: Config, path: str | None = None) -> str:
    """Write config as JSON, 0o600 (it holds secret keys). Returns the path."""
    path = path or default_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg.to_dict(), f, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def upsert_provider(cfg: Config, name: str, *, base_url: str, model: str,
                    type: str = "openai", api_key: str | None = None) -> Config:
    """Return a new Config with a NAMED provider added (or replaced if `name` exists).
    The preset binds name → endpoint → model, so selecting it points korgex at a
    self-hosted OpenAI-compatible box (vLLM, llama.cpp, a gateway) in one move.
    Immutable: builds a new Config rather than mutating the input."""
    entry = {"name": name, "type": type, "base_url": base_url, "model": model}
    if api_key:
        entry["api_key"] = api_key
    others = [p for p in cfg.providers if p.get("name") != name]
    return Config(default_model=cfg.default_model, providers=others + [entry],
                  active_provider=cfg.active_provider)


def set_active(cfg: Config, name: str) -> Config:
    """Return a new Config with `name` selected as the active provider; its model becomes
    the default, so the agent resolves to that endpoint with no extra flags."""
    p = cfg.provider_by_name(name)
    default_model = (p or {}).get("model") or cfg.default_model
    return Config(default_model=default_model, providers=list(cfg.providers),
                  active_provider=name)


def remove_provider(cfg: Config, name: str) -> Config:
    """Return a new Config with the named provider removed (clearing it as active if it
    was the selected one)."""
    others = [p for p in cfg.providers if p.get("name") != name]
    active = None if cfg.active_provider == name else cfg.active_provider
    return Config(default_model=cfg.default_model, providers=others, active_provider=active)


def resolve_model_and_key(model: str | None, cfg: Config, env: dict | None = None):
    """Resolve the active (model, api_key) pair.

    Model precedence: explicit arg → config default → ``KORGEX_MODEL`` env →
    built-in default. The key is the saved provider key for that model's provider
    type, falling back to the provider's conventional env var so users who already
    export a key keep working with no config. ``key`` may be None for keyless
    providers (e.g. local Ollama).
    """
    env = os.environ if env is None else env
    resolved = model or cfg.default_model or env.get("KORGEX_MODEL") or _BUILTIN_DEFAULT_MODEL

    ptype = provider_type_for_model(resolved)
    key = cfg.api_key_for(ptype)
    if not key:
        env_var = _ENV_KEY.get(ptype)
        if env_var:
            key = env.get(env_var)
    return resolved, key


# Default OpenAI-compatible base URLs per provider type.
_OPENROUTER_URL = "https://openrouter.ai/api/v1"
_OLLAMA_URL = "http://localhost:11434/v1"


def resolve_client_config(model: str, cfg: Config, env: dict | None = None):
    """Resolve ``(api_key, base_url)`` for a model's provider — the seam the agent
    uses to build its client from CONFIG (not just env). base_url is None for
    Anthropic (its SDK uses its own endpoint); for OpenRouter/Ollama it's the
    OpenAI-compatible URL so a `vendor/model` id or a local model routes correctly.
    Falls back to the provider's conventional env key so env-only users still work.
    """
    env = os.environ if env is None else env

    # An explicitly-selected named provider (`korgex providers use`) pins the endpoint for
    # ITS model — this is how two OpenAI-compatible boxes are told apart (provider_for by
    # type can't). A different explicit model id falls through to the normal heuristics.
    active = cfg.active()
    if active and (not model or model == active.get("model")):
        provider = active
        ptype = active.get("type", "openai")
    else:
        ptype = provider_type_for_model(model)
        # A claude-* id is unambiguous — never reroute it (not to a lone configured
        # provider, not to a custom endpoint); it always resolves to Anthropic.
        if "claude" not in (model or "").lower():
            # If the model-id heuristic doesn't match a configured provider but exactly
            # one provider IS configured, trust it — a bare local model id ("llama3.3")
            # or a gateway can't be inferred from the name alone.
            if cfg.provider_for(ptype) is None and len(cfg.providers) == 1:
                ptype = cfg.providers[0].get("type", ptype)
            # A custom OpenAI-compatible endpoint (KORGEX_API_URL — a self-hosted vLLM, an
            # LM Studio, a gateway) is an explicit signal: route an unknown model id to it
            # rather than defaulting to Anthropic, so "Qwen2.5-Coder-32B" just works.
            if ptype == "anthropic" and env.get("KORGEX_API_URL"):
                ptype = "openai"
        provider = cfg.provider_for(ptype)
    key = (provider or {}).get("api_key")
    if not key:
        env_var = _ENV_KEY.get(ptype)
        if env_var:
            key = env.get(env_var)
        key = key or env.get("KORGEX_API_KEY")

    if ptype == "anthropic":
        return key, None
    if ptype == "openrouter":
        return key, _OPENROUTER_URL
    if ptype == "ollama":
        base = (provider or {}).get("base_url") or _OLLAMA_URL
        return (key or "ollama"), base  # local needs no real key; placeholder is fine
    # openai (and any other openai-compatible, incl. a custom KORGEX_API_URL endpoint)
    base = (provider or {}).get("base_url") or env.get("KORGEX_API_URL")
    if base and not key:
        key = "EMPTY"   # self-hosted vLLM / llama.cpp accept any key — non-None so the client builds
    return key, base
