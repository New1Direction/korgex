"""Cache-aware compaction — wiring the pure cost model into the agent (Slice 2).

These exercise the agent-side glue with NO network and NO real model:
  - _call captures the provider's cache usage into self._last_cache (both shapes),
    and a telemetry hiccup never propagates / never clobbers prior state.
  - _maybe_compact threads the freeze + gate in: with NO cache state it behaves
    EXACTLY as today (size-only — keeps the baseline green); with a cached prefix it
    (a) never rewrites the frozen leading turns and (b) skips when the projected
    savings don't beat the cache discount; the compaction ledger event grows
    cache-aware fields.
"""
from src import cache_compaction as CC


class _Obj:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Led:
    def __init__(self):
        self.events = []

    def record_tool_call(self, **kw):
        self.events.append(kw)
        return len(self.events)


# ── _last_cache capture in _call ───────────────────────────────────────────────

def test_call_captures_anthropic_cache_into_last_cache(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False, model="claude-3-5-sonnet")
    assert a.provider == "anthropic"
    # default is all-zero before any call
    assert a._last_cache == {"cache_read": 0, "cache_creation": 0, "prompt_tokens": 0}

    usage = _Obj(cache_read_input_tokens=800, cache_creation_input_tokens=50,
                 input_tokens=900)
    resp = _Obj(content=[], usage=usage)

    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                return resp
    monkeypatch.setattr(a, "_gen_kwargs", lambda: {"max_tokens": 10})
    out = a._call(_Client(), [{"role": "user", "content": "hi"}], [])
    assert out is resp
    assert a._last_cache == CC.extract_cache_tokens(usage)
    assert a._last_cache["cache_read"] == 800


def test_call_captures_openai_cache_into_last_cache(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False, model="gpt-4o")
    assert a.provider == "openai"
    usage = _Obj(prompt_tokens=1000, prompt_tokens_details=_Obj(cached_tokens=600))
    resp = _Obj(choices=[], usage=usage)

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return resp
    monkeypatch.setattr(a, "_gen_kwargs", lambda: {"max_tokens": 10})
    out = a._call(_Client(), [{"role": "user", "content": "hi"}], [])
    assert out is resp
    assert a._last_cache["cache_read"] == 600
    assert a._last_cache["prompt_tokens"] == 1000


def test_call_cache_telemetry_never_raises_and_preserves_prior(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False, model="claude-3-5-sonnet")
    a._last_cache = {"cache_read": 123, "cache_creation": 0, "prompt_tokens": 200}

    class _BadUsage:
        @property
        def cache_read_input_tokens(self):
            raise RuntimeError("telemetry boom")
    resp = _Obj(content=[], usage=_BadUsage())

    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                return resp
    monkeypatch.setattr(a, "_gen_kwargs", lambda: {"max_tokens": 10})
    # must not raise; extract_cache_tokens swallows the bad field → cache_read 0,
    # but the call itself completes and returns the response.
    out = a._call(_Client(), [{"role": "user", "content": "hi"}], [])
    assert out is resp
    # capture ran (it tolerates the bad field), so _last_cache is the safe-zero read,
    # never a crash.
    assert isinstance(a._last_cache, dict)
    assert a._last_cache["cache_read"] == 0


# ── _maybe_compact: freeze + gate threaded in, degrades gracefully ─────────────

def _stub_summarizer(a, monkeypatch, text="HANDOFF: did stuff, continue Y"):
    """Wire the injected summary call so _maybe_compact runs with no network."""
    monkeypatch.setattr(a, "_get_client", lambda: object())
    monkeypatch.setattr(a, "_call", lambda *args, **k: object())
    monkeypatch.setattr(a, "_extract_final_text", lambda r: text)


def _big_openai_transcript(n=8, fat=400):
    messages = [{"role": "system", "content": "SYS"}]
    for i in range(n):
        messages.append({"role": "user" if i % 2 == 0 else "assistant",
                         "content": "blah " * fat})
    return messages


def test_maybe_compact_no_cache_degrades_to_size_only(tmp_path, monkeypatch):
    # CURRENT WORLD: _last_cache all-zero → compaction fires on size alone, exactly
    # as today. This is what keeps the 1078/1109 baseline green.
    from src.agent import KorgexAgent
    monkeypatch.setenv("KORGEX_CONTEXT_LIMIT", "1000")
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    assert a._last_cache["cache_read"] == 0
    messages = _big_openai_transcript()
    _stub_summarizer(a, monkeypatch)
    led = _Led()
    out = a._maybe_compact(messages, led, prompt_seq=1)
    assert len(out) < len(messages)                       # compacted as before
    assert out[0]["content"] == "SYS"
    assert any(e["tool_name"] == "compaction" for e in led.events)


def test_maybe_compact_freezes_cached_prefix_verbatim(tmp_path, monkeypatch):
    # With the cache covering the first few turns, those leading turns must survive
    # VERBATIM (never summarized) — rewriting them is what busts the cache.
    from src.agent import KorgexAgent
    monkeypatch.setenv("KORGEX_CONTEXT_LIMIT", "1000")
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    # distinct, small leading turns + a fat tail so savings stay high (gate passes)
    messages = [{"role": "system", "content": "SYS"}]
    messages.append({"role": "user", "content": "FROZEN-A"})
    messages.append({"role": "assistant", "content": "FROZEN-B"})
    for i in range(8):
        messages.append({"role": "user" if i % 2 == 0 else "assistant",
                         "content": "tail " * 400})

    # est tokens: SYS≈0, FROZEN-A/B ≈ 2 each; cover system + the two frozen turns.
    from src import compaction as _CP
    cover = sum(_CP.estimate_tokens([m]) for m in messages[:3]) + 1
    a._last_cache = {"cache_read": cover, "cache_creation": 0, "prompt_tokens": cover}
    _stub_summarizer(a, monkeypatch)

    led = _Led()
    out = a._maybe_compact(messages, led, prompt_seq=1)
    # the cached leading turns appear verbatim at the front, in order
    texts = [str(m.get("content")) for m in out]
    assert "FROZEN-A" in texts and "FROZEN-B" in texts
    assert texts.index("FROZEN-A") < texts.index("FROZEN-B")
    # they're not folded into the summary — they're standalone messages
    assert any(m.get("content") == "FROZEN-A" for m in out)
    # ledger event reports the frozen prefix
    ev = next(e for e in led.events if e["tool_name"] == "compaction")
    assert ev["result"]["frozen_prefix_turns"] >= 3


def test_maybe_compact_gate_skips_when_cache_cheaper(tmp_path, monkeypatch):
    # Big cached prefix + low projected savings → busting the cache costs more than
    # it saves. _maybe_compact must SKIP (return messages unchanged) and record why.
    from src.agent import KorgexAgent
    monkeypatch.setenv("KORGEX_CONTEXT_LIMIT", "1000")
    monkeypatch.setenv("KORGEX_MIN_CACHED_TOKENS", "100")
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False, model="claude-3-5-sonnet")
    assert a.provider == "anthropic"  # discount 0.9 — hard to beat
    messages = _big_openai_transcript()
    # cache covers almost the whole transcript → freezing leaves little to reclaim,
    # savings_fraction stays well under the 0.9 discount.
    from src import compaction as _CP
    nearly_all = _CP.estimate_tokens(messages[:-1])
    a._last_cache = {"cache_read": nearly_all, "cache_creation": 0,
                     "prompt_tokens": nearly_all}
    _stub_summarizer(a, monkeypatch)

    led = _Led()
    out = a._maybe_compact(messages, led, prompt_seq=1)
    assert out is messages                       # skipped — unchanged identity
    # a skip is recorded with a reason so trace/verify can show the decision
    skip = [e for e in led.events if e["tool_name"] == "compaction"]
    assert skip and skip[-1]["result"]["decision_reason"] == "cache_cheaper"


def test_maybe_compact_ledger_event_has_cache_fields(tmp_path, monkeypatch):
    # When compaction DOES fire with cache state present, the event carries the
    # cache-aware fields so korgex trace/verify can prove the decision.
    from src.agent import KorgexAgent
    monkeypatch.setenv("KORGEX_CONTEXT_LIMIT", "1000")
    monkeypatch.setenv("KORGEX_MIN_CACHED_TOKENS", "1")
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False, model="gpt-4o")
    assert a.provider == "openai"  # discount 0.5 — easy to beat with a fat tail
    # small cached prefix, fat tail → high savings, gate passes
    messages = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "small"}]
    for i in range(8):
        messages.append({"role": "user" if i % 2 == 0 else "assistant",
                         "content": "tail " * 400})
    a._last_cache = {"cache_read": 5, "cache_creation": 2, "prompt_tokens": 5}
    _stub_summarizer(a, monkeypatch)

    led = _Led()
    out = a._maybe_compact(messages, led, prompt_seq=7)
    assert len(out) < len(messages)  # fired
    ev = next(e for e in led.events if e["tool_name"] == "compaction")
    r = ev["result"]
    for k in ("cache_read_before", "cache_creation_before", "frozen_prefix_turns",
              "savings_fraction", "decision_reason"):
        assert k in r, f"missing {k}"
    assert r["cache_read_before"] == 5
    assert r["cache_creation_before"] == 2
    assert ev["triggered_by"] == 7
