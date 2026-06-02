"""Typed stall classifier — name WHICH kind of stuck, not just 'thinking…'.

The worst agent UX is silence indistinguishable from a hang. This classifies the
run's state into a typed verdict so the agent/operator can tell a genuinely-busy
run from a stuck one — and catches the highest-value failure: `false_completion`
(claims done, produced no deliverable). Pure + deterministic (no LLM).

Categories: working | complete | looping | narrating | asking | false_completion
(benign: working/complete; the rest are 'stuck' signals).
"""
from src import stall_classifier as SC


def _sig(**kw):
    base = dict(text="", had_tool_call=False, repeat_streak=0,
                produced_artifact=False, expects_artifact=False)
    base.update(kw)
    return SC.Signals(**base)


def test_had_tool_call_is_working():
    v = SC.classify(_sig(had_tool_call=True))
    assert v.category == "working" and not v.is_stuck()


def test_looping_when_repeat_streak_high():
    v = SC.classify(_sig(repeat_streak=5, had_tool_call=True))
    assert v.category == "looping" and v.is_stuck()  # looping outranks 'working'


def test_false_completion_claims_done_but_no_artifact():
    v = SC.classify(_sig(text="All done — the feature is implemented.",
                         expects_artifact=True, produced_artifact=False))
    assert v.category == "false_completion" and v.is_stuck()
    assert "deliver" in v.reason.lower() or "no" in v.reason.lower()


def test_real_completion_claims_done_with_artifact():
    v = SC.classify(_sig(text="Done — added the function and tests pass.",
                         expects_artifact=True, produced_artifact=True))
    assert v.category == "complete" and not v.is_stuck()


def test_completion_on_a_readonly_task_is_complete_not_false():
    # A question-answering task expects no artifact; claiming done is fine.
    v = SC.classify(_sig(text="The handler lives in app.py line 10.",
                         expects_artifact=False, produced_artifact=False))
    assert v.category == "complete" and not v.is_stuck()


def test_narrating_intent_without_action():
    v = SC.classify(_sig(text="Let me search the codebase for the bug.",
                         had_tool_call=False))
    assert v.category == "narrating" and v.is_stuck()


def test_asking_a_question_without_acting():
    v = SC.classify(_sig(text="Which database should I migrate first?",
                         had_tool_call=False, expects_artifact=True))
    assert v.category == "asking" and v.is_stuck()


def test_confidence_is_between_0_and_1():
    v = SC.classify(_sig(had_tool_call=True))
    assert 0.0 <= v.confidence <= 1.0


def test_claims_complete_detector():
    assert SC.claims_complete("All done!")
    assert SC.claims_complete("the task is complete.")
    assert SC.claims_complete("Finished — everything works.")
    assert not SC.claims_complete("I'm still working on the parser.")


def test_is_question_detector():
    assert SC.is_question("Which file should I edit?")
    assert SC.is_question("Should I proceed with the migration?")
    assert not SC.is_question("I edited the file.")


# ── integration: false-completion diagnosed at the agent's finish ──────────────

class _Led:
    def __init__(self): self.events = []
    def record_tool_call(self, **kw): self.events.append(kw); return len(self.events)
    def record_user_prompt(self, p, triggered_by=None): return 1
    def record_llm_call(self, **kw): return 1


def test_agent_records_false_completion_when_done_but_unmutated(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    a = KorgexAgent(model="claude-sonnet-4-6", interactive=False,
                    repo_root=str(tmp_path), ledger=_Led())
    # the task asks for a CHANGE (action verb), but the model just claims done
    def fake_call(client, messages, tools_payload, system_prompt=None, system_volatile=None):
        class R: pass
        r = R(); r._text = "All done — implemented the feature."; r.usage = None; r._calls = []
        return r
    monkeypatch.setattr(a, "_get_client", lambda: object())
    monkeypatch.setattr(a, "_call", fake_call)
    monkeypatch.setattr(a, "_extract_tool_calls", lambda r: r._calls)
    monkeypatch.setattr(a, "_extract_final_text", lambda r: r._text)
    monkeypatch.setattr(a, "_assistant_turn", lambda r: {"role": "assistant", "content": r._text})

    a.run_task("implement the rate limiter")  # action verb → expects a deliverable
    cats = [e["result"].get("category") for e in a.ledger.events if e["tool_name"] == "stall.verdict"]
    assert "false_completion" in cats, "claiming done with no mutation on a change-task must flag false_completion"


def test_agent_records_complete_on_a_question(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    a = KorgexAgent(model="claude-sonnet-4-6", interactive=False,
                    repo_root=str(tmp_path), ledger=_Led())
    def fake_call(client, messages, tools_payload, system_prompt=None, system_volatile=None):
        class R: pass
        r = R(); r._text = "The handler is in app.py at line 10."; r.usage = None; r._calls = []
        return r
    monkeypatch.setattr(a, "_get_client", lambda: object())
    monkeypatch.setattr(a, "_call", fake_call)
    monkeypatch.setattr(a, "_extract_tool_calls", lambda r: r._calls)
    monkeypatch.setattr(a, "_extract_final_text", lambda r: r._text)
    monkeypatch.setattr(a, "_assistant_turn", lambda r: {"role": "assistant", "content": r._text})

    a.run_task("where is the request handler?")  # a question → expects no deliverable
    cats = [e["result"].get("category") for e in a.ledger.events if e["tool_name"] == "stall.verdict"]
    assert cats and "false_completion" not in cats
