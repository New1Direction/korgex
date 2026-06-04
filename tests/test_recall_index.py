"""Fast, better-ranked recall via a stdlib SQLite FTS5 (BM25) index over the ledger.

The substring scorer requires every query term to appear (brittle) and ranks by raw
occurrence. FTS5 ranks by BM25 — partial matches allowed, rarer terms weighted more,
path/identifier tokens split — with no new dependency (FTS5 is built into Python's sqlite3).
"""
from src import recall_index as RI
from src import recall as R


def _ev(seq, tool, args, tb=None):
    return {"seq_id": seq, "tool_name": tool, "args": args, "triggered_by": tb}


def test_fts_available_here():
    assert RI.fts_available() is True                       # FTS5 ships with the stdlib sqlite3


def test_search_fts_ranks_by_relevance_not_raw_occurrence():
    events = [
        _ev(1, "user_prompt", {"prompt": "set up auth middleware"}),
        _ev(2, "Edit", {"file_path": "src/auth/middleware.py"}),
        _ev(3, "Edit", {"file_path": "src/unrelated/widget.py"}),
        _ev(4, "Bash", {"command": "pytest tests/test_auth.py"}),
    ]
    seqs = [h["event"]["seq_id"] for h in RI.search_fts(events, "auth middleware", top_n=10)]
    assert set(seqs) == {1, 2, 4}                           # widget (#3) has neither term → excluded
    assert seqs[0] in (1, 2)                                # a both-terms doc ranks first
    assert seqs[-1] == 4                                    # the single-term doc ranks last
    assert all(h["score"] >= -1e9 for h in RI.search_fts(events, "auth", top_n=10))  # higher = better


def test_search_fts_tokenizes_paths_and_is_not_strict_and():
    # 'health' must match a path token; not every term need appear (BM25, not strict AND)
    events = [
        _ev(1, "user_prompt", {"prompt": "add a healthz endpoint"}),
        _ev(2, "Edit", {"file_path": "src/api/health.py"}),
    ]
    seqs = [h["event"]["seq_id"] for h in RI.search_fts(events, "health api", top_n=10)]
    assert 2 in seqs                                        # matched via split path tokens


def test_search_fts_is_injection_safe_and_empty_on_no_match():
    events = [_ev(1, "Edit", {"file_path": "a.py"})]
    assert RI.search_fts(events, '"; DROP TABLE docs; --', top_n=5) == []   # punctuation can't inject
    assert RI.search_fts(events, "", top_n=5) == []                          # no terms → no results


def test_recall_search_mode_fts_routes_through_the_index():
    events = [_ev(1, "Edit", {"file_path": "src/auth.py"}),
              _ev(2, "Edit", {"file_path": "src/cart.py"})]
    seqs = [h["event"]["seq_id"] for h in R.search(events, "auth", top_n=5, mode="fts")]
    assert seqs == [1]                                      # only the auth edit matches
