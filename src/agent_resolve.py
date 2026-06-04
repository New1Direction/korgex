"""Provider / model / mode / subagent RESOLUTION — pure helpers lifted out of the
agent loop so the core stays focused and these stay independently testable. No `self`
and no loop state: model->provider(OAuth) mapping, BYO-OAuth token minting, the
read-only subagent tool subset, mode->params, and model-id resolution."""
from __future__ import annotations

import os
from typing import Optional

from src.tool_abstraction import USER_TOOLS


def _looks_anthropic(model_id: str) -> bool:
    """True for any Claude model — direct Anthropic, OpenRouter (anthropic/claude-...), etc."""
    m = (model_id or "").lower()
    return "claude" in m or m.startswith("anthropic/")


# Bring-your-own-OAuth/key gateways: the OpenAI-compatible endpoint each speaks.
# The agent loop authenticates with a bearer token from the SAME local credential
# the provider's own CLI uses (reusing the model_router loaders), so no separate
# api-key is needed. Claude and Gemini are intentionally NOT here — their local
# tokens are rejected by the public endpoints (Claude Code OAuth → raw Anthropic
# API; Antigravity/ADC OAuth → generativelanguage 401), so both fall back to a
# configured api-key (e.g. OpenRouter). See _get_client.
_OAUTH_BASE_URLS = {
    "grok": "https://api.x.ai/v1",
    "nous": "https://inference-api.nousresearch.com/v1",
    "venice": "https://api.venice.ai/api/v1",
}


def _oauth_provider_for(model_id: str) -> Optional[str]:
    """Map a model to its BYO-OAuth provider (currently grok), or None.

    Consults the DEFAULT_MODELS registry first (by alias key or concrete model_id),
    then falls back to a substring heuristic on the model name. (Gemini/Claude are
    excluded — their local tokens are rejected by the public endpoints.)
    """
    m = (model_id or "").lower()
    if not m:
        return None
    try:
        from src.model_router import DEFAULT_MODELS
        cfg = DEFAULT_MODELS.get(model_id)
        if cfg is None:
            cfg = next((c for c in DEFAULT_MODELS.values() if c.model_id == model_id), None)
        if cfg and cfg.provider in _OAUTH_BASE_URLS:
            return cfg.provider
    except Exception:
        pass
    if "grok" in m:
        return "grok"
    return None


def _oauth_token_and_base(provider: str):
    """Mint a bearer token from the local OAuth credential for a BYO provider.

    Returns ``(token, base_url)``. token is None when no credential is available,
    so the caller falls back to the configured api-key path. Reuses the
    model_router loaders so there's one source of truth per provider.
    """
    base = _OAUTH_BASE_URLS.get(provider)
    try:
        from src.model_router import GrokClient, NousClient
        if provider == "nous":
            return (NousClient()._ensure_key() or None), base   # mints the agent-key
        if provider == "venice":
            return (os.environ.get("VENICE_API_KEY") or None), base  # api-key, not OAuth
        return (GrokClient()._ensure_token() or None), base     # grok (xAI OAuth)
    except Exception:
        return None, base


# Read-only tool subset handed to non-mutating subagents (Recall is read-only).
_READONLY_SUBAGENT_TOOLS = ["Read", "Grep", "Glob", "Recall"]

# Map the Agent tool's model alias → a concrete model id.
_MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def subagent_tools(subagent_type: str) -> list:
    """Tool name subset a subagent of `subagent_type` is allowed to use.

    Read-only types (explore/plan/review/research) get search/read tools only.
    The default ("code") gets every tool EXCEPT Agent and Orchestrate — a
    subagent must not recursively spawn subagents OR fan out a DAG of them
    (nesting is one level deep: only the top-level agent orchestrates, its
    children are leaves → bounded blast radius, ledger depth, and threads).
    """
    if subagent_type in ("explore", "plan", "review", "research"):
        return list(_READONLY_SUBAGENT_TOOLS)
    return [name for name in USER_TOOLS.keys() if name not in ("Agent", "Orchestrate")]


def _resolve_params(mode: str) -> dict:
    """Per-mode generation params (max_tokens / thinking budget / temperature).

    Wires MODE_PARAMS (previously dead code) into the loop. No mode → the prior
    default (max_tokens=4096) so non-mode behavior is unchanged.
    """
    if mode:
        try:
            from src.model_router import MODE_PARAMS
            if mode in MODE_PARAMS:
                return dict(MODE_PARAMS[mode])
        except Exception:
            pass
    return {"max_tokens": 4096}


def _resolve_model(model: str, mode: str) -> str:
    """Pick the active model.

    Precedence: explicit --model → --mode → the configured default (`korgex setup`)
    → KORGEX_MODEL env → built-in Sonnet 4.6. Consulting config.default_model here
    is load-bearing: without it, `korgex "task"` ignored the model the user picked
    in setup and crashed with a provider/key mismatch (e.g. an OpenRouter user's key
    sent to Anthropic as x-api-key → 401).
    """
    if model:
        try:
            from src.model_router import DEFAULT_MODELS
            if model in DEFAULT_MODELS:          # short alias → concrete API model id
                return DEFAULT_MODELS[model].model_id
        except Exception:
            pass
        return model
    if mode:
        try:
            from src.model_router import MODE_MODEL_MAP, DEFAULT_MODELS
            key = MODE_MODEL_MAP.get(mode)
            if key and key in DEFAULT_MODELS:
                return DEFAULT_MODELS[key].model_id
        except Exception:
            pass  # fall through to config/env/default
    try:
        from src.config import load_config
        cfg_default = load_config().default_model
    except Exception:
        cfg_default = None  # config missing/unreadable → fall through, never crash
    return cfg_default or os.environ.get("KORGEX_MODEL") or "claude-sonnet-4-6"
