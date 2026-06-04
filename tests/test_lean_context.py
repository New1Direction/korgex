"""Lean context from the verifiable ledger — retrieve, don't carry.

Instead of feeding a model the whole history every turn, pull the few past ledger
events relevant to the step and render a compact, provenance-stamped block. Short
prompts → cheaper/faster inference → a smaller (even self-hosted) model runs the same
loop. Documentation-first: each line says WHAT happened; the seq id is how you check
it (`korgex why` / `korgex verify`).
"""
from src import lean_context as LC
from src import korg_ledger as KL


def _journal(tmp_path):
    jp = str(tmp_path / "j.jsonl")
    c = KL.LocalJournalClient(journal_path=jp)
    root = c.record_user_prompt("add a healthz endpoint to the api")
    c.record_llm_call("gpt-4o", 100, 50, 200, triggered_by=root)
    c.record_tool_call("Edit", {"file_path": "src/api/health.py"}, {"ok": True}, True, 10, triggered_by=root)
    c.record_tool_call("Bash", {"command": "pytest -q tests/test_health.py"}, {"exit": 0}, True, 900, triggered_by=root)
    c.record_tool_call("Edit", {"file_path": "src/billing.py"}, {"ok": True}, True, 8, triggered_by=root)
    return KL.load_journal_raw(jp)


# ── token estimate ────────────────────────────────────────────────────────────

def test_estimate_tokens_grows_with_text():
    assert LC.estimate_tokens("") == 0
    assert LC.estimate_tokens("a" * 400) > LC.estimate_tokens("a" * 40)


# ── one documentation-first line per event ────────────────────────────────────

def test_summarize_event_is_documentation_first_with_seq(tmp_path):
    events = _journal(tmp_path)
    edit = next(e for e in events if e.get("tool_name") == "Edit" and "health" in str(e.get("args")))
    line = LC.summarize_event(edit)
    assert "src/api/health.py" in line          # WHAT happened, in plain terms
    assert f"#{edit['seq_id']}" in line          # the verifiable handle


def test_summarize_skips_low_signal_inference(tmp_path):
    events = _journal(tmp_path)
    inf = next(e for e in events if (e.get("tool_name") or e.get("event_type")) == "llm_inference")
    assert LC.summarize_event(inf) == ""         # thinking rounds aren't context, they're noise


# ── the builder: retrieve relevant, attach provenance, honor a budget ─────────

def test_build_lean_context_includes_relevant_excludes_unrelated(tmp_path):
    events = _journal(tmp_path)
    ctx = LC.build_lean_context(events, "health", budget_tokens=500)
    assert "src/api/health.py" in ctx["text"]    # relevant edit retrieved
    assert "src/billing.py" not in ctx["text"]   # unrelated edit left out
    assert ctx["refs"] and all(isinstance(r, int) for r in ctx["refs"])


def test_build_lean_context_honors_token_budget(tmp_path):
    events = _journal(tmp_path)
    # "health" matches the prompt, the health.py edit, and the test_health pytest run
    tiny = LC.build_lean_context(events, "health", budget_tokens=8)
    big = LC.build_lean_context(events, "health", budget_tokens=2000)
    assert 1 <= tiny["events_used"] < big["events_used"]    # always keep one; budget genuinely caps the rest
    assert tiny["tokens_est"] <= big["tokens_est"]


# ── provenance: every cited memory traces back to the chain ───────────────────

def test_unresolved_refs_flags_only_fabricated_ones(tmp_path):
    events = _journal(tmp_path)
    ctx = LC.build_lean_context(events, "health", budget_tokens=500)
    assert LC.unresolved_refs(ctx["refs"], events) == []        # all retrieved refs are real
    assert LC.unresolved_refs([999999], events) == [999999]     # a fabricated ref is caught
