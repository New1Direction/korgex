"""MMR re-ranking — diversify recall results so near-duplicate events don't crowd the
lean-context budget. Maximal Marginal Relevance: pick the candidate that's relevant AND
dissimilar to what's already picked, balanced by lambda (1.0 = pure relevance, lower =
more diversity). Pure + LLM-free; complements the FTS/BM25 recall.
"""
from src import recall as R


def _ev(seq, tool, args):
    return {"seq_id": seq, "tool_name": tool, "args": args}


def test_mmr_prefers_a_distinct_event_over_a_near_duplicate():
    a = _ev(1, "Edit", {"file_path": "src/auth.py"})
    b = _ev(2, "Edit", {"file_path": "src/auth.py"})              # near-duplicate of a
    c = _ev(3, "Bash", {"command": "pytest tests/test_billing.py"})  # distinct
    hits = [{"event": a, "score": 1.0}, {"event": b, "score": 0.5}, {"event": c, "score": 0.5}]
    order = [h["event"]["seq_id"] for h in R.mmr_rerank(hits, lambda_=0.7)]
    assert order[0] == 1                                          # most-relevant stays first
    assert order.index(3) < order.index(2)                       # distinct (#3) beats near-dup (#2)


def test_mmr_preserves_order_when_all_distinct():
    hits = [{"event": _ev(1, "Edit", {"file_path": "a.py"}), "score": 1.0},
            {"event": _ev(2, "Bash", {"command": "pytest"}), "score": 0.8},
            {"event": _ev(3, "Read", {"file_path": "readme.md"}), "score": 0.6}]
    order = [h["event"]["seq_id"] for h in R.mmr_rerank(hits, lambda_=0.7)]
    assert order == [1, 2, 3]                                    # distinct + descending → unchanged


def test_mmr_lambda_one_is_pure_relevance():
    a = _ev(1, "Edit", {"file_path": "src/auth.py"})
    b = _ev(2, "Edit", {"file_path": "src/auth.py"})
    c = _ev(3, "Bash", {"command": "pytest"})
    hits = [{"event": a, "score": 1.0}, {"event": b, "score": 0.9}, {"event": c, "score": 0.5}]
    order = [h["event"]["seq_id"] for h in R.mmr_rerank(hits, lambda_=1.0)]
    assert order == [1, 2, 3]                                    # lambda=1 → diversity off


def test_mmr_empty_and_singleton():
    assert R.mmr_rerank([]) == []
    one = [{"event": _ev(1, "Edit", {"file_path": "a.py"}), "score": 1.0}]
    assert [h["event"]["seq_id"] for h in R.mmr_rerank(one)] == [1]


def test_build_lean_context_diversify_keeps_a_distinct_file(tmp_path):
    from src import lean_context as LC
    from src import korg_ledger as KL
    jp = str(tmp_path / "j.jsonl")
    c = KL.LocalJournalClient(journal_path=jp)
    root = c.record_user_prompt("do the work")                 # 'auth' deliberately NOT in the prompt
    for _ in range(3):
        c.record_tool_call("Edit", {"file_path": "src/auth/session_manager.py"},
                           {"ok": True}, True, 5, triggered_by=root)
    c.record_tool_call("Edit", {"file_path": "src/auth/token_store.py"},
                       {"ok": True}, True, 5, triggered_by=root)
    events = KL.load_journal_raw(jp)
    # a budget that fits ~2 action lines
    div = LC.build_lean_context(events, "auth", budget_tokens=24, mode="fts", diversify=True)
    plain = LC.build_lean_context(events, "auth", budget_tokens=24, mode="fts", diversify=False)
    assert "token_store.py" in div["text"]                     # the distinct file survives MMR
    assert "token_store.py" not in plain["text"]               # crowded out by the 3 near-dup edits
