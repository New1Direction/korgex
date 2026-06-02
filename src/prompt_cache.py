"""Cross-vendor prompt caching.

Keep the expensive, STABLE prefix — the system prompt and the tool definitions —
in the provider's prompt cache so repeated turns skip reprocessing it. The payoff
is a faster first token and a cheaper call, which is most of the per-turn latency
once streaming is live.

Every provider shares one rule: **the cached prefix must be byte-identical across
turns.** So volatile per-turn content (the live task list) is kept OUT of the
cached system prompt and carried separately — for Anthropic as a trailing,
unmarked ``system`` block; on the OpenAI-compatible path simply by leaving it out
of the (stable) system message. This mirrors OpenRouter's own guidance: "move
dynamic content into a later user message instead of appending it after a cached
block in the first system message."

Where providers DIVERGE is how a cache is requested:

  • OpenAI, Google Gemini, xAI Grok, DeepSeek — cache AUTOMATICALLY (≥1024 tok),
    no marker needed (and api.openai.com rejects the extra field), so we send a
    plain string and let the provider do it.
  • Anthropic Claude, Alibaba Qwen — need a MANUAL ``cache_control`` breakpoint on
    the last block to cache. A breakpoint on the system prompt caches everything
    ahead of it in the prefix too — the tool array is sent before the system
    prompt, so a single marker caches BOTH the 48-tool payload and the system
    text.

This module is the one place that knows those rules; the agent's call path just
asks it for the right shape.
"""
from __future__ import annotations

EPHEMERAL = {"type": "ephemeral"}

# Model families OpenRouter requires an explicit cache_control breakpoint for.
# Everything else caches automatically, so a marker is at best a no-op and at
# worst (api.openai.com) a hard error.
_MANUAL_FAMILIES = ("anthropic", "claude", "qwen")


def is_openrouter(base_url: str | None) -> bool:
    """True when requests route through OpenRouter (its caching extensions apply)."""
    return bool(base_url) and "openrouter" in base_url.lower()


def needs_manual_breakpoint(model: str | None) -> bool:
    """True for model families that require a manual ``cache_control`` breakpoint
    (Anthropic Claude, Alibaba Qwen). Auto-cache families return False."""
    m = (model or "").lower()
    return any(fam in m for fam in _MANUAL_FAMILIES)


def should_mark(provider: str, base_url: str | None, model: str | None) -> bool:
    """Whether to add explicit cache markers in the OpenAI-COMPATIBLE request.

    Only on OpenRouter, only for a manual-breakpoint model. Pure OpenAI rejects
    the field; auto-cache models gain nothing. The native Anthropic SDK path does
    its own marking via ``anthropic_system``/``with_tool_cache`` and is excluded.
    """
    return (
        provider == "openai"
        and is_openrouter(base_url)
        and needs_manual_breakpoint(model)
    )


# ── Anthropic native SDK shaping ─────────────────────────────────────────────

def anthropic_system(stable: str, volatile: str | None = None) -> list:
    """The Anthropic ``system`` param as content blocks.

    The stable text carries the cache breakpoint; volatile text (the task list)
    trails as a SEPARATE, unmarked block so it can change every turn without
    invalidating the cached prefix.
    """
    blocks = [{"type": "text", "text": stable, "cache_control": dict(EPHEMERAL)}]
    if volatile:
        blocks.append({"type": "text", "text": volatile})
    return blocks


def with_tool_cache(tools: list | None) -> list | None:
    """Mark the LAST tool with a cache breakpoint so the entire (stable) tool
    array is cached. Returns a new list — the input is never mutated. Empty/None
    pass through unchanged.
    """
    if not tools:
        return tools
    out = [dict(t) for t in tools]
    out[-1] = {**out[-1], "cache_control": dict(EPHEMERAL)}
    return out


# ── OpenAI-compatible (OpenRouter) shaping ───────────────────────────────────

def openai_system_message(stable: str, *, cache: bool) -> dict:
    """The system message for the OpenAI-compatible path.

    When ``cache`` (OpenRouter → a manual-breakpoint model), content is a parts
    array with a ``cache_control`` marker; otherwise a plain string, which is what
    auto-cache providers want and the only thing api.openai.com will accept.
    """
    if cache:
        return {
            "role": "system",
            "content": [
                {"type": "text", "text": stable, "cache_control": dict(EPHEMERAL)},
            ],
        }
    return {"role": "system", "content": stable}


def openai_task_reminder(volatile: str | None) -> dict | None:
    """The volatile task list as a TRAILING system message for the OpenAI-compatible
    path. Trailing (after the cached prefix) means it steers the model every turn
    without invalidating the cached system+tools prefix. ``None`` when empty."""
    if not volatile:
        return None
    return {"role": "system", "content": volatile}


def openai_cache_extra(provider: str, base_url: str | None, model: str | None) -> dict:
    """OpenRouter ``extra_body`` for caching. A top-level ``cache_control`` makes
    OpenRouter auto-advance the breakpoint as the conversation grows, so the
    message HISTORY is cached too — not just the system prompt. Returns ``{}`` for
    providers that cache automatically (nothing to add).
    """
    if should_mark(provider, base_url, model):
        return {"cache_control": dict(EPHEMERAL)}
    return {}
