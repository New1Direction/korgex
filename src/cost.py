"""Estimated dollar-cost from the ledger.

Every ``llm_inference`` event records its token counts, so the spend is recoverable:
tokens × list price. Be honest about what's provable: the TOKEN COUNTS come from the
tamper-evident ledger (re-verify with ``korgex verify``), but the DOLLARS are an
*estimate* — prices aren't recorded in the ledger and change over time. An unknown
model is counted in tokens and never fabricated into a price.

Prices are public list prices in USD per 1M tokens (input, output), approximate and
provider-list as of authoring. Matched by substring so a provider prefix
(``openai/gpt-4o``) resolves the same as the bare id.
"""
from __future__ import annotations

# (substring, $/1M input tokens, $/1M output tokens). Order matters: most specific
# first (e.g. gpt-4o-mini before gpt-4o). Unmatched models price as unknown.
_PRICES = [
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.0),
    ("o3-mini", 1.10, 4.40),
    ("o3", 10.0, 40.0),
    ("claude-opus", 15.0, 75.0),
    ("claude-sonnet", 3.0, 15.0),
    ("claude-haiku", 0.80, 4.0),
    # Gemini — most specific first (substring matching)
    ("gemini-3.1-pro", 1.25, 10.0),
    ("gemini-3.1-flash", 0.15, 0.60),
    ("gemini-2.5-pro", 1.25, 10.0),
    ("gemini-2.5-flash", 0.15, 0.60),
    ("gemini-2.0-flash", 0.10, 0.40),
    ("gemini-pro", 1.25, 10.0),
    ("gemini-flash", 0.15, 0.60),
    ("gemini", 0.30, 2.50),
    ("venice-uncensored", 0.50, 2.0),
    ("venice", 0.50, 2.0),           # catch-all, last
    ("deepseek", 0.27, 1.10),
    ("grok-reasoning", 2.0, 80.0),
    ("grok", 2.0, 10.0),
    ("qwen", 0.40, 1.20),
    ("llama", 0.10, 0.40),
]


def price_for(model: str):
    """(input_$/Mtok, output_$/Mtok) for a model id, matched by substring, or None
    if the model isn't in the table (so the caller never invents a price)."""
    m = (model or "").lower()
    for needle, pin, pout in _PRICES:
        if needle in m:
            return (pin, pout)
    return None


def _cache_rates(model: str):
    """(cache-read discount, cache-creation multiplier) for a model's cached prompt
    tokens. Anthropic reads cost ~1/10th (90% off) and writes carry a ~25% surcharge;
    every other (OpenAI-compatible) provider gets ~50% off reads and no write
    surcharge. The read discounts mirror ``cache_compaction.CACHE_READ_DISCOUNT`` —
    pricing facts, an estimate like everything else in this module."""
    m = (model or "").lower()
    if "claude" in m or "anthropic" in m:
        return (0.9, 1.25)
    return (0.5, 1.0)


def _int(x) -> int:
    """Coerce a token count to int, tolerating the ledger's redacted/missing values
    (e.g. '[REDACTED]', None) → 0 rather than crashing."""
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def estimate_cost(events) -> dict:
    """Roll up token usage + estimated USD from the ledger's llm_inference events.

    Cache-aware: when an event carries the disjoint prompt-cache breakdown
    (``uncached_input_tokens`` + ``cache_read_tokens`` + ``cache_creation_tokens``,
    recorded since the verifiable-cache change), cached reads are priced at their real
    (discounted) rate instead of the full input rate, and Anthropic cache writes carry
    their surcharge. Without the breakdown (older events), it's the exact legacy
    behavior: ``prompt_tokens × input_rate``. A token is never counted twice and the
    reported ``input_tokens`` is the TRUE billable input (cached reads included).

    Returns {total_usd, input_tokens, output_tokens, by_model, unknown_models,
    cache_read_tokens, cache_savings_usd}."""
    by_model: dict = {}
    unknown = []
    total_in = total_out = 0
    total_usd = 0.0
    total_cache_read = 0
    total_savings = 0.0
    for e in events or []:
        if (e.get("tool_name") or e.get("event_type")) != "llm_inference":
            continue
        args = e.get("args") or {}
        result = e.get("result") or {}
        model = args.get("model") or "?"
        pout_tok = _int(result.get("completion_tokens"))

        # Prefer the disjoint breakdown; fall back to the legacy single figure.
        uncached_raw = args.get("uncached_input_tokens")
        has_breakdown = uncached_raw is not None
        cache_read = _int(args.get("cache_read_tokens"))
        cache_creation = _int(args.get("cache_creation_tokens"))
        if has_breakdown:
            uncached = _int(uncached_raw)
            input_count = uncached + cache_read + cache_creation
        else:
            uncached = _int(args.get("prompt_tokens"))
            input_count = uncached

        total_in += input_count
        total_out += pout_tok
        total_cache_read += cache_read

        slot = by_model.setdefault(model, {"input": 0, "output": 0, "usd": 0.0, "known": False})
        slot["input"] += input_count
        slot["output"] += pout_tok
        price = price_for(model)
        if price is None:
            if model not in unknown:
                unknown.append(model)
            continue
        in_rate, out_rate = price
        if has_breakdown:
            read_disc, creation_mult = _cache_rates(model)
            input_usd = (uncached * in_rate
                         + cache_read * in_rate * (1 - read_disc)
                         + cache_creation * in_rate * creation_mult) / 1_000_000
            total_savings += cache_read * in_rate * read_disc / 1_000_000
        else:
            input_usd = uncached * in_rate / 1_000_000
        usd = input_usd + pout_tok / 1_000_000 * out_rate
        slot["usd"] += usd
        slot["known"] = True
        total_usd += usd
    return {
        "total_usd": round(total_usd, 6),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "by_model": by_model,
        "unknown_models": unknown,
        "cache_read_tokens": total_cache_read,
        "cache_savings_usd": round(total_savings, 6),
    }


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_cost(summary: dict) -> str:
    """One-line estimate: ``≈ $0.0123 (est.) · 12.3k in + 4.5k out tokens``. Surfaces
    prompt-cache savings when any, and notes unknown models so the figure is never
    silently incomplete."""
    s = summary or {}
    ins, outs = s.get("input_tokens", 0), s.get("output_tokens", 0)
    if not ins and not outs:
        return "no model calls recorded yet"
    usd = s.get("total_usd", 0.0)
    line = f"≈ ${usd:.4f} (est.) · {_fmt_tok(ins)} in + {_fmt_tok(outs)} out tokens"
    cache_read = s.get("cache_read_tokens", 0)
    if cache_read:
        saved = s.get("cache_savings_usd", 0.0)
        line += f"  ·  cache: {_fmt_tok(cache_read)} read, saved ≈ ${saved:.4f}"
    unknown = s.get("unknown_models") or []
    if unknown:
        line += f"  ·  {len(unknown)} unpriced model(s): {', '.join(unknown[:3])}"
    return line
