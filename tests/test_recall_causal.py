"""Causal retrieval — follow the ledger's `triggered_by` DAG, not just text matches.

Flat-text search returns only events that literally match the query. But korgex writes
a causal chain: every event records what triggered it. So retrieval can do what
incumbents can't — a matched action pulls in the prompt that caused it (the "why"), and
a matched prompt pulls in the actions it triggered (the "what happened"). That's
coherent context assembled from the verifiable chain, zero new dependencies.
"""
from src import recall as R
from src import lean_context as LC


def _prompt(seq, text):
    return {"seq_id": seq, "tool_name": "user_prompt", "args": {"prompt": text}, "triggered_by": None}


def _act(seq, tool, args, trig):
    return {"seq_id": seq, "tool_name": tool, "args": args, "triggered_by": trig}


# ── expand_causal: walk the triggered_by edges both ways ───────────────────────

def test_expand_causal_pulls_in_the_triggering_cause(tmp_path):
    events = [
        _prompt(1, "set up the database layer"),
        _act(2, "Edit", {"file_path": "migrations/001.sql"}, 1),
    ]
    out = R.expand_causal(events, [events[1]], depth=1)     # seed = the edit
    assert {e["seq_id"] for e in out} == {1, 2}             # + the prompt that caused it


def test_expand_causal_pulls_in_the_triggered_effects(tmp_path):
    events = [
        _prompt(1, "investigate the flaky checkout test"),
        _act(2, "Edit", {"file_path": "src/cart.py"}, 1),
        _act(3, "Bash", {"command": "pytest"}, 1),
    ]
    out = R.expand_causal(events, [events[0]], depth=1)     # seed = the prompt
    assert {1, 2, 3} <= {e["seq_id"] for e in out}         # + the actions it triggered


def test_expand_causal_causes_only_excludes_unrelated_siblings(tmp_path):
    # one prompt triggered two unrelated edits; matching one edit must pull its cause
    # (the prompt) but NOT the sibling — this is what keeps the live loop's context clean
    events = [
        _prompt(1, "add a healthz endpoint"),
        _act(2, "Edit", {"file_path": "src/health.py"}, 1),
        _act(3, "Edit", {"file_path": "src/billing.py"}, 1),
    ]
    seqs = {e["seq_id"] for e in R.expand_causal(events, [events[1]], depth=1, direction="causes")}
    assert 1 in seqs                                       # the cause (why this edit happened)
    assert 3 not in seqs                                   # the unrelated sibling stays out


def test_expand_causal_is_bounded_and_deduped(tmp_path):
    chain = [_act(i, "Edit", {}, (i - 1 if i > 1 else None)) for i in range(1, 11)]
    out = R.expand_causal(chain, [chain[0]], depth=9, max_total=4)
    seqs = [e["seq_id"] for e in out]
    assert len(out) <= 4                                    # bounded
    assert len(seqs) == len(set(seqs))                      # deduped
    assert 1 in seqs                                        # the seed is kept


# ── build_lean_context(causal=True): the cause rides along ─────────────────────

def test_build_lean_context_causal_adds_the_cause_opt_in(tmp_path):
    events = [
        _prompt(1, "set up the database layer"),
        _act(2, "Edit", {"file_path": "migrations/001_init.sql"}, 1),
    ]
    # the query matches ONLY the migration edit, not the prompt's words
    plain = LC.build_lean_context(events, "migrations", budget_tokens=500, causal=False)
    caus = LC.build_lean_context(events, "migrations", budget_tokens=500, causal=True)

    assert "migrations/001_init.sql" in plain["text"]
    assert "set up the database" not in plain["text"]       # default: cause not pulled
    assert "set up the database" in caus["text"]            # causal: the triggering prompt rides along
    assert 1 in caus["refs"] and 2 in caus["refs"]          # both trace to the chain
