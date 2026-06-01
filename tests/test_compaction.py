"""Model-authored context compaction — summarize-and-continue for long runs.

Today a long agent run just grows `messages` toward the model's context limit and
eventually fails; the only bound is an iteration cap. This compacts: when the
transcript gets large, the model writes its own handoff summary and history is
rebuilt as [head + most-recent-raw-turns-to-a-budget + summary], so the run
continues with intent preserved. These tests pin the pure pieces; the summary
model call is injected.
"""
from src import compaction as C


def _msg(role, text, n=1):
    return {"role": role, "content": text * n}


# ── token estimate + trigger ──────────────────────────────────────────────────

def test_estimate_tokens_grows_with_text():
    small = [_msg("user", "hi")]
    big = [_msg("user", "word " , 5000)]
    assert C.estimate_tokens(big) > C.estimate_tokens(small)


def test_should_compact_only_when_over_threshold():
    small = [_msg("user", "x", 10)]
    assert C.should_compact(small, limit_tokens=100_000) is False
    huge = [_msg("user", "x", 4_000_00)]  # ~hundreds of k chars
    assert C.should_compact(huge, limit_tokens=100_000) is True


def test_should_not_compact_tiny_history_even_with_small_limit():
    # Never compact when there's basically nothing to compact (≤ a couple turns).
    assert C.should_compact([_msg("user", "hello")], limit_tokens=1) is False


# ── rebuild: head + recent-raw (backfilled to budget) + summary ────────────────

def test_rebuild_keeps_head_and_appends_summary():
    head = [_msg("system", "SYS")]
    history = [_msg("user", "u1"), _msg("assistant", "a1"),
               _msg("user", "u2"), _msg("assistant", "a2")]
    out = C.rebuild_with_summary(head, history, summary="SUMMARY",
                                 recent_budget_tokens=1)  # tiny → keep only the last turn
    assert out[0]["content"] == "SYS"                       # head preserved
    assert any("SUMMARY" in str(m.get("content")) for m in out)  # summary present
    # the summary is the LAST message (the model resumes from it)
    assert "SUMMARY" in str(out[-1]["content"])


def test_rebuild_backfills_recent_turns_within_budget():
    head = [_msg("system", "SYS")]
    # 6 small turns; a generous budget should keep several recent ones verbatim
    history = [_msg("user" if i % 2 == 0 else "assistant", f"turn{i} ") for i in range(6)]
    out = C.rebuild_with_summary(head, history, summary="S", recent_budget_tokens=10_000)
    kept = [m for m in out if str(m.get("content", "")).startswith("turn")]
    assert len(kept) >= 2  # most recent turns survive verbatim
    # recent turns kept in order, ending just before the summary
    assert "turn5" in str(kept[-1]["content"])


def test_rebuild_is_smaller_than_original_for_big_history():
    head = [_msg("system", "SYS")]
    history = [_msg("user", "blah ", 2000) for _ in range(20)]
    out = C.rebuild_with_summary(head, history, summary="tiny summary",
                                 recent_budget_tokens=500)
    assert C.estimate_tokens(out) < C.estimate_tokens(head + history)


# ── compact_messages: the injectable orchestrator (no network) ─────────────────

def test_compact_messages_uses_injected_summarizer():
    head = [_msg("system", "SYS")]
    history = [_msg("user", "do X"), _msg("assistant", "did X"),
               _msg("user", "now Y"), _msg("assistant", "working")]

    def fake_summarize(msgs):
        return "HANDOFF: built X, mid-Y"

    out = C.compact_messages(head, history, summarize=fake_summarize,
                             recent_budget_tokens=1)
    assert any("HANDOFF" in str(m.get("content")) for m in out)
    assert out[0]["content"] == "SYS"


def test_compact_messages_failsafe_returns_original_on_summarizer_error():
    head = [_msg("system", "SYS")]
    history = [_msg("user", "a"), _msg("assistant", "b")]

    def boom(msgs):
        raise RuntimeError("model down")

    out = C.compact_messages(head, history, summarize=boom, recent_budget_tokens=1)
    # A failed summary must NOT lose the conversation — return it intact.
    assert out == head + history


# ── integration: _maybe_compact through the agent ──────────────────────────────

class _Led:
    def __init__(self): self.events = []
    def record_tool_call(self, **kw): self.events.append(kw); return len(self.events)


def test_agent_maybe_compact_fires_and_records(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    monkeypatch.setenv("KORGEX_CONTEXT_LIMIT", "1000")  # tiny → easy to exceed
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)

    # a big OpenAI-shaped transcript (system head + many fat turns)
    messages = [{"role": "system", "content": "SYS"}]
    for i in range(8):
        messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": "blah " * 400})

    # stub the model call used by the summarizer
    monkeypatch.setattr(a, "_get_client", lambda: object())
    monkeypatch.setattr(a, "_call", lambda *args, **k: object())
    monkeypatch.setattr(a, "_extract_final_text", lambda r: "HANDOFF: did stuff, continue Y")

    led = _Led()
    out = a._maybe_compact(messages, led, prompt_seq=1)

    assert len(out) < len(messages)                       # it compacted
    assert out[0]["content"] == "SYS"                     # head preserved
    assert any("HANDOFF" in str(m.get("content")) for m in out)  # summary present
    assert any(e["tool_name"] == "compaction" for e in led.events)  # ledgered


def test_agent_maybe_compact_noop_when_small(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    monkeypatch.delenv("KORGEX_CONTEXT_LIMIT", raising=False)
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    small = [{"role": "user", "content": "hi"}]
    assert a._maybe_compact(small, _Led(), 1) is small  # unchanged identity → no-op
