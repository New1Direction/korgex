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
