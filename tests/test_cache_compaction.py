"""Cache-aware compaction — the pure cost model (Slice 2).

Provider prompt-caching and compaction FIGHT: rewriting the cached prefix busts
the cache for zero benefit. These tests pin the pure pieces that let compaction
decide WHEN it actually pays to compact:

  - extract_cache_tokens: normalize Anthropic vs OpenAI usage shapes (attr OR dict).
  - update_frozen_prefix: how many LEADING turns fall inside the provider-cached
    prefix (never compact inside it).
  - should_force_compaction / discount_for: only bust the cache when the projected
    savings beat the cache-read discount AND there's a meaningful cached prefix.

All pure + offline — synthetic usage numbers, an injected token estimator. No model,
no network.
"""
from src import cache_compaction as CC


# ── helper: attr-style usage objects (mimic SDK response.usage) ────────────────

class _Obj:
    """A tiny attr-bag so tests can build SDK-shaped usage objects."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ── extract_cache_tokens: both providers, both shapes ──────────────────────────

def test_extract_anthropic_attr_shape():
    usage = _Obj(cache_read_input_tokens=900, cache_creation_input_tokens=100,
                 input_tokens=1000)
    out = CC.extract_cache_tokens(usage)
    # Anthropic input_tokens is the NEW (uncached) input — cache tokens are billed
    # ON TOP, so uncached_input == input_tokens and they don't overlap.
    assert out == {"cache_read": 900, "cache_creation": 100,
                   "prompt_tokens": 1000, "uncached_input": 1000}


def test_extract_openai_attr_shape():
    # OpenAI nests the cache hit under prompt_tokens_details.cached_tokens
    usage = _Obj(prompt_tokens=1000,
                 prompt_tokens_details=_Obj(cached_tokens=900))
    out = CC.extract_cache_tokens(usage)
    assert out["cache_read"] == 900
    assert out["prompt_tokens"] == 1000
    assert out["cache_creation"] == 0  # OpenAI doesn't separate creation
    # OpenAI prompt_tokens INCLUDES the cached subset — the full-rate part is the
    # remainder, so the cost model never double-counts the cached tokens.
    assert out["uncached_input"] == 100


def test_extract_dict_shape_both_providers():
    anth = {"cache_read_input_tokens": 50, "cache_creation_input_tokens": 5,
            "input_tokens": 60}
    assert CC.extract_cache_tokens(anth) == {
        "cache_read": 50, "cache_creation": 5, "prompt_tokens": 60,
        "uncached_input": 60}
    oai = {"prompt_tokens": 200, "prompt_tokens_details": {"cached_tokens": 120}}
    out = CC.extract_cache_tokens(oai)
    assert out["cache_read"] == 120 and out["prompt_tokens"] == 200
    assert out["uncached_input"] == 80  # 200 total − 120 cached


def test_extract_uncached_input_is_disjoint_breakdown():
    # The whole point: uncached_input + cache_read + cache_creation is the TRUE
    # billable input, with no token counted twice, for either provider shape.
    anth = _Obj(cache_read_input_tokens=900, cache_creation_input_tokens=100,
                input_tokens=1000)
    a = CC.extract_cache_tokens(anth)
    assert a["uncached_input"] + a["cache_read"] + a["cache_creation"] == 2000
    oai = _Obj(prompt_tokens=1000, prompt_tokens_details=_Obj(cached_tokens=900))
    o = CC.extract_cache_tokens(oai)
    assert o["uncached_input"] + o["cache_read"] + o["cache_creation"] == 1000


def test_extract_openai_garbage_cache_never_makes_uncached_negative():
    # cached_tokens larger than prompt_tokens is impossible but must clamp, not go < 0
    bad = _Obj(prompt_tokens=100, prompt_tokens_details=_Obj(cached_tokens=999))
    out = CC.extract_cache_tokens(bad)
    assert out["uncached_input"] == 0


def test_extract_none_and_missing_fields_are_zero_never_raises():
    assert CC.extract_cache_tokens(None) == {
        "cache_read": 0, "cache_creation": 0, "prompt_tokens": 0, "uncached_input": 0}
    assert CC.extract_cache_tokens(_Obj()) == {
        "cache_read": 0, "cache_creation": 0, "prompt_tokens": 0, "uncached_input": 0}
    # a details object that raises on attribute access must not blow up
    class _Raises:
        @property
        def cached_tokens(self):
            raise RuntimeError("boom")
    bad = _Obj(prompt_tokens=10, prompt_tokens_details=_Raises())
    out = CC.extract_cache_tokens(bad)
    assert out["cache_read"] == 0 and out["prompt_tokens"] == 10
    assert out["uncached_input"] == 10


# ── update_frozen_prefix: leading turns inside the provider-cached prefix ───────

def _est_by_len(msgs):
    """A toy single-message estimator: 1 token per char of content. The real one is
    compaction.estimate_tokens; here we inject so there's no agent dependency."""
    return sum(len(str(m.get("content", ""))) for m in msgs)


def test_frozen_prefix_counts_leading_turns_until_cache_total():
    # 4 turns of 100 "chars" each → est 100 tokens apiece with _est_by_len.
    messages = [{"role": "user", "content": "x" * 100} for _ in range(4)]
    # cache covers the first 1000 prompt tokens, but each turn here is 100, so the
    # first 3 turns (300) fit and the gate is "accumulate until >= cache_read".
    assert CC.update_frozen_prefix(messages, 300, _est_by_len) == 3
    # exactly hitting the boundary mid-turn still freezes that turn (we never compact
    # a turn the provider may have partially cached).
    assert CC.update_frozen_prefix(messages, 250, _est_by_len) == 3


def test_frozen_prefix_zero_when_no_cache():
    messages = [{"role": "user", "content": "x" * 100} for _ in range(4)]
    assert CC.update_frozen_prefix(messages, 0, _est_by_len) == 0


def test_frozen_prefix_capped_at_message_count():
    messages = [{"role": "user", "content": "x" * 10} for _ in range(3)]
    # cache claims far more tokens than the transcript holds → freeze everything,
    # never more than len(messages).
    assert CC.update_frozen_prefix(messages, 10_000, _est_by_len) == 3


def test_frozen_prefix_uses_injected_estimator_real_signature():
    # The real call passes compaction.estimate_tokens; prove the injected fn is
    # called with a single-message list (so a per-turn estimate is summed).
    seen = []

    def est(msgs):
        seen.append(len(msgs))
        return 50

    messages = [{"role": "user", "content": "a"} for _ in range(5)]
    CC.update_frozen_prefix(messages, 120, est)  # 50,100,150 → freeze 3
    assert seen and all(n == 1 for n in seen)  # always called per single message


# ── should_force_compaction / discount_for: the cost-model gate ────────────────

def test_force_when_reclaim_beats_cache_bust_cost():
    # ABSOLUTE economics: reclaiming 5000 tokens beats the 0.9*5000=4500-token
    # one-time cost of busting the cache, and cached is well over the floor.
    assert CC.should_force_compaction(
        reclaimed_tokens=5000, cached_tokens=5000,
        cache_read_discount=0.9, min_cached_tokens=1000) is True


def test_no_force_when_reclaim_below_cache_bust_cost():
    # Reclaiming only 1000 < the 4500-token bust cost → keep the cache.
    assert CC.should_force_compaction(
        reclaimed_tokens=1000, cached_tokens=5000,
        cache_read_discount=0.9, min_cached_tokens=1000) is False


def test_no_force_when_cached_below_floor():
    # Too little cached to bother, even reclaiming a lot.
    assert CC.should_force_compaction(
        reclaimed_tokens=10_000, cached_tokens=10,
        cache_read_discount=0.9, min_cached_tokens=1000) is False


def test_force_at_floor_boundary_is_inclusive():
    # cached exactly at the floor counts; 600 reclaimed beats 0.5*1000=500 cost.
    assert CC.should_force_compaction(
        reclaimed_tokens=600, cached_tokens=1000,
        cache_read_discount=0.5, min_cached_tokens=1000) is True


def test_warm_anthropic_session_still_compacts_when_reclaim_is_large():
    # Regression: the old fraction-vs-rate gate demanded >90% reclaim on Anthropic
    # (discount 0.9) and so DISABLED compaction. Absolute economics fires correctly:
    # reclaiming 10k beats the 0.9*2000=1800 bust cost.
    assert CC.should_force_compaction(
        reclaimed_tokens=10_000, cached_tokens=2000,
        cache_read_discount=0.9, min_cached_tokens=1000) is True


def test_discount_for_known_and_unknown_providers():
    assert CC.discount_for("anthropic") == 0.9
    assert CC.discount_for("openai") == 0.5
    # unknown/local → conservative (smaller) discount, never raises
    assert CC.discount_for("ollama") == 0.5
    assert CC.discount_for(None) == 0.5
    assert CC.discount_for("ANTHROPIC") == 0.9  # case-insensitive
