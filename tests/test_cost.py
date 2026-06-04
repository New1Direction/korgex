"""Tests for ledger dollar-cost (src/cost.py).

The ledger records token counts on every llm_inference event. This estimates the
spend: tokens × public list prices. The honesty line that the tests pin: the TOKENS
are provable (from the tamper-evident ledger), the DOLLARS are an estimate (prices
aren't recorded and change), and an unknown model is counted in tokens but never
fabricated into a price.
"""
from src import cost as C


def _llm(model, pin, pout):
    return {"seq_id": 1, "tool_name": "llm_inference",
            "args": {"model": model, "prompt_tokens": pin},
            "result": {"completion_tokens": pout}}


def _llm_cached(model, *, uncached, cache_read, cache_creation, pout, prompt_tokens=None):
    """An llm_inference event carrying the disjoint cache breakdown the agent now
    records. prompt_tokens defaults to the OpenAI-style total (uncached+read+create);
    cost.py must price from the breakdown, not this field."""
    if prompt_tokens is None:
        prompt_tokens = uncached + cache_read + cache_creation
    return {"seq_id": 1, "tool_name": "llm_inference",
            "args": {"model": model, "prompt_tokens": prompt_tokens,
                     "cache_read_tokens": cache_read,
                     "cache_creation_tokens": cache_creation,
                     "uncached_input_tokens": uncached},
            "result": {"completion_tokens": pout}}


class TestPriceFor:
    def test_matches_known_models_by_substring(self):
        assert C.price_for("openai/gpt-4o") == C.price_for("gpt-4o")     # provider prefix ignored
        assert C.price_for("anthropic/claude-sonnet-4-6") is not None
        assert C.price_for("gpt-4o-mini") != C.price_for("gpt-4o")       # mini is its own (cheaper) row

    def test_unknown_model_is_none(self):
        assert C.price_for("some/unknown-model-x") is None


class TestEstimateCost:
    def test_sums_tokens_and_prices_a_known_model(self):
        s = C.estimate_cost([_llm("openai/gpt-4o", 1_000_000, 1_000_000)])
        assert s["input_tokens"] == 1_000_000
        assert s["output_tokens"] == 1_000_000
        pin, pout = C.price_for("gpt-4o")
        assert abs(s["total_usd"] - (pin + pout)) < 1e-6     # 1M in + 1M out = in$+out$
        assert "openai/gpt-4o" in s["by_model"]

    def test_unknown_model_counts_tokens_but_not_dollars(self):
        s = C.estimate_cost([_llm("mystery/model", 500, 500)])
        assert s["input_tokens"] == 500 and s["output_tokens"] == 500
        assert s["total_usd"] == 0.0                         # never fabricated
        assert "mystery/model" in s["unknown_models"]

    def test_multiple_models_accumulate(self):
        s = C.estimate_cost([_llm("gpt-4o", 1000, 0), _llm("claude-haiku-4-5", 1000, 0)])
        assert s["input_tokens"] == 2000
        assert s["total_usd"] > 0

    def test_redacted_or_garbage_token_counts_dont_crash(self):
        # the ledger redacts some fields → prompt_tokens can be '[REDACTED]'/None
        evts = [
            {"tool_name": "llm_inference", "args": {"model": "gpt-4o", "prompt_tokens": "[REDACTED]"},
             "result": {"completion_tokens": None}},
            _llm("gpt-4o", 1000, 500),
        ]
        s = C.estimate_cost(evts)
        assert s["input_tokens"] == 1000 and s["output_tokens"] == 500   # garbage → 0, real counted

    def test_no_llm_events_is_zero(self):
        s = C.estimate_cost([{"seq_id": 1, "tool_name": "Edit", "args": {"file_path": "a"}}])
        assert s["total_usd"] == 0.0 and s["input_tokens"] == 0


class TestCacheAwareCost:
    """The honest-cost fix: a cached prompt token isn't billed at full input rate.
    Anthropic reads are ~90% cheaper and writes carry a ~25% surcharge; OpenAI reads
    are ~50% cheaper. cost.py prices the disjoint breakdown the ledger now records."""

    def test_anthropic_cache_read_is_discounted_90pct(self):
        # 100k full-rate input + 900k read from cache. Old code billed all 1M at full
        # rate (it actually IGNORED Anthropic cache tokens entirely → undercount);
        # the breakdown bills reads at 1/10th.
        in_rate, out_rate = C.price_for("claude-sonnet-4-6")
        s = C.estimate_cost([_llm_cached(
            "claude-sonnet-4-6", uncached=100_000, cache_read=900_000,
            cache_creation=0, pout=0)])
        expected = (100_000 * in_rate + 900_000 * in_rate * 0.1) / 1_000_000
        assert abs(s["total_usd"] - expected) < 1e-6
        # the true input total includes the cached reads (not just the new tokens)
        assert s["input_tokens"] == 1_000_000

    def test_anthropic_cache_creation_carries_a_surcharge(self):
        # A cold turn WRITES the cache: those tokens cost ~1.25× the base input rate.
        in_rate, _ = C.price_for("claude-sonnet-4-6")
        s = C.estimate_cost([_llm_cached(
            "claude-sonnet-4-6", uncached=0, cache_read=0,
            cache_creation=1000, pout=0)])
        expected = (1000 * in_rate * 1.25) / 1_000_000
        assert abs(s["total_usd"] - expected) < 1e-9
        assert s["input_tokens"] == 1000

    def test_openai_cache_read_is_discounted_50pct(self):
        in_rate, _ = C.price_for("gpt-4o")
        s = C.estimate_cost([_llm_cached(
            "gpt-4o", uncached=100, cache_read=900, cache_creation=0, pout=0)])
        expected = (100 * in_rate + 900 * in_rate * 0.5) / 1_000_000
        assert abs(s["total_usd"] - expected) < 1e-9
        assert s["input_tokens"] == 1000

    def test_breakdown_is_used_over_a_misleading_prompt_tokens(self):
        # prompt_tokens here is deliberately wrong; the disjoint breakdown wins.
        in_rate, _ = C.price_for("gpt-4o")
        s = C.estimate_cost([_llm_cached(
            "gpt-4o", uncached=100, cache_read=900, cache_creation=0, pout=0,
            prompt_tokens=999_999)])
        expected = (100 * in_rate + 900 * in_rate * 0.5) / 1_000_000
        assert abs(s["total_usd"] - expected) < 1e-9

    def test_reports_cache_savings(self):
        in_rate, _ = C.price_for("claude-sonnet-4-6")
        s = C.estimate_cost([_llm_cached(
            "claude-sonnet-4-6", uncached=0, cache_read=900_000,
            cache_creation=0, pout=0)])
        # saved = what the reads WOULD have cost at full rate minus what they did cost
        saved = 900_000 * in_rate * 0.9 / 1_000_000
        assert abs(s["cache_savings_usd"] - saved) < 1e-6
        assert s["cache_read_tokens"] == 900_000

    def test_legacy_events_without_breakdown_are_unchanged(self):
        # No cache fields → exact old behavior: full input rate, no savings line.
        in_rate, out_rate = C.price_for("gpt-4o")
        s = C.estimate_cost([_llm("gpt-4o", 1000, 500)])
        expected = (1000 * in_rate + 500 * out_rate) / 1_000_000
        assert abs(s["total_usd"] - expected) < 1e-9
        assert s["cache_read_tokens"] == 0
        assert s["cache_savings_usd"] == 0.0

    def test_unknown_model_with_cache_counts_tokens_no_dollars(self):
        s = C.estimate_cost([_llm_cached(
            "mystery/model", uncached=100, cache_read=900, cache_creation=0, pout=0)])
        assert s["input_tokens"] == 1000      # true total still counted
        assert s["total_usd"] == 0.0          # never fabricated
        assert "mystery/model" in s["unknown_models"]

    def test_format_surfaces_cache_savings(self):
        s = C.estimate_cost([_llm_cached(
            "claude-sonnet-4-6", uncached=0, cache_read=900_000,
            cache_creation=0, pout=0)])
        out = C.format_cost(s)
        assert "cache" in out.lower()
        assert "saved" in out.lower()


class TestFormat:
    def test_format_mentions_dollars_tokens_and_estimate(self):
        s = C.estimate_cost([_llm("gpt-4o", 12345, 6789)])
        out = C.format_cost(s)
        assert "$" in out
        assert "est" in out.lower()          # honest: it's an estimate
        assert "tok" in out.lower()

    def test_format_empty_is_friendly(self):
        out = C.format_cost(C.estimate_cost([]))
        assert isinstance(out, str)
