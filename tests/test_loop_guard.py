"""Loop safety rails — kill two classic agent-loop pathologies, deterministically.

1. Repeat/doom detection: the agent retries the SAME failing tool call forever.
   Hash (name, args); after N consecutive identical FAILED calls, warn; after M,
   force a different approach (block the repeat). Success resets the counter.
2. Tool-intent (narrating-not-acting): the model says "let me search…" but emits
   NO tool call. Detect the intent and nudge it to actually call the tool; stop
   nudging after a cap so it can't loop on the nudge either.

Both are pure + deterministic (no LLM), so they're cheap and testable.
"""
from src import loop_guard as LG


# ── repeat / doom detection ────────────────────────────────────────────────────

def test_distinct_calls_never_trip():
    g = LG.RepeatGuard(warn_at=3, force_at=5)
    for i in range(10):
        assert g.check(f"Bash", {"command": f"echo {i}"}, failed=True) == "ok"


def test_repeated_success_does_not_trip():
    g = LG.RepeatGuard(warn_at=3, force_at=5)
    for _ in range(10):
        assert g.check("Read", {"file_path": "a.py"}, failed=False) == "ok"


def test_repeated_failure_warns_then_forces():
    g = LG.RepeatGuard(warn_at=3, force_at=5)
    call = ("Bash", {"command": "pytest"})
    results = [g.check(*call, failed=True) for _ in range(6)]
    # first two ok, 3rd-4th warn, 5th+ force
    assert results[0] == "ok" and results[1] == "ok"
    assert "warn" in results[2]
    assert results[4] == "force" and results[5] == "force"


def test_a_different_call_resets_the_streak():
    g = LG.RepeatGuard(warn_at=3, force_at=5)
    c = ("Bash", {"command": "pytest"})
    g.check(*c, failed=True); g.check(*c, failed=True)        # streak 2
    g.check("Read", {"file_path": "x"}, failed=True)           # different → reset
    assert g.check(*c, failed=True) == "ok"                    # streak back to 1


def test_success_after_failures_resets():
    g = LG.RepeatGuard(warn_at=3, force_at=5)
    c = ("Bash", {"command": "pytest"})
    for _ in range(4):
        g.check(*c, failed=True)        # into warn territory
    assert g.check(*c, failed=False) == "ok"   # it finally worked → reset
    assert g.check(*c, failed=True) == "ok"    # streak restarts at 1


def test_args_hash_is_order_independent():
    g = LG.RepeatGuard(warn_at=3, force_at=5)
    a = ("Edit", {"file_path": "x", "old": "1"})
    b = ("Edit", {"old": "1", "file_path": "x"})  # same args, different key order
    g.check(*a, failed=True); g.check(*b, failed=True)
    assert "warn" in g.check(*a, failed=True)  # counted as the same call → streak 3


# ── tool-intent (narrating instead of acting) ─────────────────────────────────

def test_detects_stated_intent_without_action():
    assert LG.looks_like_unacted_intent("Let me search the codebase for the handler.")
    assert LG.looks_like_unacted_intent("I'll read the config file now.")
    assert LG.looks_like_unacted_intent("Now I will run the tests to confirm.")


def test_plain_answer_is_not_flagged():
    assert not LG.looks_like_unacted_intent("The bug is a missing semicolon on line 4.")
    assert not LG.looks_like_unacted_intent("Done — all 3 tests pass.")


def test_intent_nudge_caps_out():
    g = LG.IntentGuard(max_nudges=2)
    assert g.nudge() is not None       # 1
    assert g.nudge() is not None       # 2
    assert g.nudge() is None           # capped — stop nudging, let it finish


# ── integration: the rails through the agent loop ──────────────────────────────

class _Led:
    def __init__(self): self.events = []
    def record_tool_call(self, **kw): self.events.append(kw); return len(self.events)
    def record_user_prompt(self, p, triggered_by=None): return 1
    def record_llm_call(self, **kw): return 1


def _stub_agent(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    a = KorgexAgent(model="claude-sonnet-4-6", interactive=False,
                    repo_root=str(tmp_path), ledger=_Led())
    monkeypatch.setattr(a, "_get_client", lambda: object())
    monkeypatch.setattr(a, "_extract_final_text", lambda r: getattr(r, "_text", ""))
    monkeypatch.setattr(a, "_assistant_turn", lambda r: {"role": "assistant", "content": ""})
    monkeypatch.setattr(a, "_tool_result_turn", lambda cid, res: {"role": "user", "content": str(res)})
    return a


def test_repeat_rail_blocks_a_failing_loop(tmp_path, monkeypatch):
    a = _stub_agent(tmp_path, monkeypatch)
    # the model calls the same Bash command every turn; it always "fails"
    def fake_call(client, messages, tools_payload, system_prompt=None, system_volatile=None):
        class R: pass
        r = R(); r._text = ""; r.usage = None
        r._calls = [{"id": "c", "name": "Bash", "args": {"command": "pytest"}}]
        return r
    monkeypatch.setattr(a, "_call", fake_call)
    monkeypatch.setattr(a, "_extract_tool_calls", lambda r: r._calls)
    monkeypatch.setattr(a, "_dispatch_call", lambda call, seq, tf=None: {"error": "boom"})
    monkeypatch.setenv("KORGEX_MAX_ITERATIONS", "8")

    a.run_task("run the tests")
    led = a.ledger
    assert any(e.get("tool_name") == "loop_guard.repeat_block" for e in led.events), \
        "the repeat rail should fire on a stuck failing loop"


def test_intent_rail_nudges_then_lets_finish(tmp_path, monkeypatch):
    a = _stub_agent(tmp_path, monkeypatch)
    texts = iter(["Let me search for the handler.",   # intent, no tool → nudge
                  "The handler is in app.py, line 10."])  # real answer → finish
    def fake_call(client, messages, tools_payload, system_prompt=None, system_volatile=None):
        class R: pass
        r = R(); r._text = next(texts); r.usage = None; r._calls = []
        return r
    monkeypatch.setattr(a, "_call", fake_call)
    monkeypatch.setattr(a, "_extract_tool_calls", lambda r: r._calls)

    res = a.run_task("where is the handler?")
    led = a.ledger
    assert any(e.get("tool_name") == "loop_guard.intent_nudge" for e in led.events), \
        "narrating without a tool call should trigger a nudge"
    assert res["success"] is True and "app.py" in res["result"]
