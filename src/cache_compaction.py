"""Cache-aware compaction — the pure cost model.

Provider prompt-caching and compaction work against each other: rewriting the
cached prefix of a transcript busts the cache (the next request pays full price for
those tokens again) for ZERO benefit. This module gives compaction the numbers it
needs to make that trade correctly:

  - ``extract_cache_tokens(usage)`` — normalize the two provider usage shapes
    (Anthropic ``cache_read_input_tokens`` / ``cache_creation_input_tokens``;
    OpenAI ``prompt_tokens_details.cached_tokens``) into one flat dict. Tolerant of
    attr-objects OR dicts and of missing/garbage fields (everything defaults to 0,
    never raises — telemetry must not break a turn).
  - ``update_frozen_prefix(messages, cache_read_tokens, est_tokens_fn)`` — how many
    LEADING turns fall inside the provider-cached prefix. Those turns must never be
    rewritten by compaction (rewriting them is what busts the cache).
  - ``should_force_compaction(...)`` / ``discount_for(provider)`` — the gate: only
    bust the cache when projected savings beat the cache-read discount AND there's a
    meaningful cached prefix to lose. Below that, compacting costs more than it saves.

Everything here is pure + offline: synthetic usage numbers and an injected token
estimator, no model and no network.
"""
from __future__ import annotations

# Per-provider cache-read discount: a cached prompt token costs this fraction LESS
# than an uncached one (Anthropic ~90% cheaper on reads; OpenAI ~50%). Compaction
# that rewrites a cached prefix forfeits exactly this saving on the next turn, so we
# only do it when re-summarizing saves MORE than the discount we'd give up.
CACHE_READ_DISCOUNT = {"anthropic": 0.9, "openai": 0.5}

# Unknown/local providers: assume the conservative (smaller) discount so we don't
# over-eagerly bust a cache we can't price.
_DEFAULT_DISCOUNT = 0.5


def _get(obj, name, default=0):
    """Read ``name`` off a dict OR an attr-object, tolerating absence and accessors
    that raise. Returns ``default`` (0) on anything unexpected — never raises."""
    try:
        if isinstance(obj, dict):
            val = obj.get(name, default)
        else:
            val = getattr(obj, name, default)
    except Exception:
        return default
    if val is None:
        return default
    return val


def _as_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def extract_cache_tokens(usage) -> dict:
    """Normalize a provider ``usage`` object into
    ``{"cache_read", "cache_creation", "prompt_tokens", "uncached_input"}`` (all ints).

    Anthropic: ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` /
    ``input_tokens``. OpenAI: ``prompt_tokens`` with the cache hit nested under
    ``prompt_tokens_details.cached_tokens``. Tolerant of dict OR attr shapes and of
    missing/garbage fields — defaults everything to 0 and never raises.

    The two providers count differently, and that difference is the whole reason a
    naive ``prompt_tokens × rate`` cost is wrong:

      • **Anthropic** ``input_tokens`` is the NEW (uncached) input only — the cached
        reads and writes are billed ON TOP, so they do NOT overlap it.
      • **OpenAI** ``prompt_tokens`` is the TOTAL prompt, with the cached reads a
        SUBSET of it.

    ``uncached_input`` resolves that here (where the raw shape is visible) into one
    disjoint figure: the full-rate input tokens, with no token also counted in
    ``cache_read``/``cache_creation``. So ``uncached_input + cache_read +
    cache_creation`` is the true billable input for either provider, and a consumer
    (cost) never has to re-guess the convention from counts alone."""
    if usage is None:
        return {"cache_read": 0, "cache_creation": 0, "prompt_tokens": 0,
                "uncached_input": 0}

    # cache_read: Anthropic field first, else OpenAI's nested cached_tokens. Track
    # which path produced it — it pins the counting convention below.
    anthropic_read = _as_int(_get(usage, "cache_read_input_tokens", 0))
    cache_read = anthropic_read
    if not cache_read:
        details = _get(usage, "prompt_tokens_details", None)
        if details is not None:
            cache_read = _as_int(_get(details, "cached_tokens", 0))

    cache_creation = _as_int(_get(usage, "cache_creation_input_tokens", 0))

    # prompt_tokens: Anthropic calls it input_tokens, OpenAI prompt_tokens.
    raw_prompt = _as_int(_get(usage, "prompt_tokens", 0))
    prompt_tokens = raw_prompt or _as_int(_get(usage, "input_tokens", 0))

    # Anthropic shape: input_tokens (no prompt_tokens), or an Anthropic-only cache
    # field is present. Then prompt_tokens is ALREADY the uncached part. Otherwise
    # (OpenAI) the cached read is inside prompt_tokens, so subtract it out (clamped).
    is_anthropic_shape = (raw_prompt == 0) or anthropic_read > 0 or cache_creation > 0
    if is_anthropic_shape:
        uncached_input = prompt_tokens
    else:
        uncached_input = max(0, prompt_tokens - cache_read - cache_creation)

    return {
        "cache_read": cache_read,
        "cache_creation": cache_creation,
        "prompt_tokens": prompt_tokens,
        "uncached_input": uncached_input,
    }


def update_frozen_prefix(messages, cache_read_tokens, est_tokens_fn) -> int:
    """Return how many LEADING messages fall inside the provider-cached prefix.

    Walk the transcript front-to-back, summing the estimated tokens of each turn
    (via the injected ``est_tokens_fn``, called on a single-message list) until the
    running total reaches ``cache_read_tokens`` — that many leading turns are
    "frozen" and must never be rewritten by compaction (rewriting them busts the
    cache). With no cache (``cache_read_tokens <= 0``) nothing is frozen. Capped at
    ``len(messages)``. Pure: the estimator is injected so there's no agent dep."""
    if cache_read_tokens <= 0:
        return 0
    total = 0
    frozen = 0
    for msg in messages:
        try:
            total += int(est_tokens_fn([msg]))
        except Exception:
            # A bad estimate must not crash the gate; just stop counting here.
            break
        frozen += 1
        if total >= cache_read_tokens:
            break
    return min(frozen, len(messages))


def discount_for(provider) -> float:
    """Cache-read discount for a provider id (case-insensitive). Unknown/local
    providers get the conservative default so we never over-eagerly bust a cache we
    can't price. Never raises."""
    key = (provider or "").strip().lower()
    return CACHE_READ_DISCOUNT.get(key, _DEFAULT_DISCOUNT)


def should_force_compaction(reclaimed_tokens, cached_tokens,
                            cache_read_discount, min_cached_tokens) -> bool:
    """The gate, in ABSOLUTE token economics: True only when compacting actually pays.

    Compaction rewrites the cached prefix, forfeiting the cache-read discount on those
    cached tokens next turn — a one-time cost of roughly ``cache_read_discount *
    cached_tokens``. It's worth it ONLY when the tokens reclaimed by re-summarizing
    (``reclaimed_tokens`` = before − projected) EXCEED that cost, and there's a
    meaningful cached prefix to begin with (``cached_tokens`` ≥ ``min_cached_tokens``).

    (A fraction-vs-rate comparison — savings_fraction > discount — is dimensionally
    wrong: it would demand a >90% reclaim on a warm Anthropic session and so
    effectively DISABLE compaction for the default provider.)"""
    return (cached_tokens >= min_cached_tokens
            and reclaimed_tokens > cache_read_discount * cached_tokens)
