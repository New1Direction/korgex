"""Anchored-tip verification: the fix for the unkeyed-chain regeneration hole.

An unkeyed hash chain is only tamper-evident against a reference you already
trust. A forger can edit a body and re-link + re-hash the WHOLE downstream chain
so it is internally consistent again — `verify_chain` alone then returns []. The
only way to detect that is to compare the chain's tip against a genuine tip that
was published/anchored externally before the forgery. These tests pin that down.
"""
from src import ledger_spec as S


def _chain(bodies):
    """Build a minimal, valid agent_message chain from a list of bodies."""
    evs, prev = [], S.GENESIS_HASH
    for i, b in enumerate(bodies, 1):
        e = {"schema_version": "1.0", "seq_id": i, "tool_name": "agent_message",
             "args": {"body": b}, "prev_hash": prev}
        e["entry_hash"] = S.chain_hash(e)
        prev = e["entry_hash"]
        evs.append(e)
    return evs


def _regenerate(events):
    """Re-link and re-hash a chain after a content edit, so a forger's tail is
    internally self-consistent again (this is exactly the attack)."""
    prev = S.GENESIS_HASH
    for e in events:
        e["prev_hash"] = prev
        e.pop("entry_hash", None)
        e["entry_hash"] = S.chain_hash(e)
        prev = e["entry_hash"]
    return events


def test_anchored_tip_accepts_the_genuine_chain():
    evs = _chain(["tabs win", "spaces win", "you monster"])
    tip = evs[-1]["entry_hash"]
    assert S.verify_chain(evs, expected_tip=tip) == []


def test_anchored_tip_rejects_a_fully_regenerated_forgery():
    evs = _chain(["tabs win", "spaces win", "you monster"])
    genuine_tip = evs[-1]["entry_hash"]
    evs[0]["args"]["body"] = "spaces win, actually"   # forge the first line...
    _regenerate(evs)                                  # ...then repair the whole chain
    assert S.verify_chain(evs) == []                  # naive check: internally consistent, passes
    errs = S.verify_chain(evs, expected_tip=genuine_tip)
    assert errs and any("tip" in e.lower() for e in errs)   # anchored check catches it


def test_without_an_anchor_the_forgery_is_invisible():
    evs = _chain(["tabs win", "spaces win", "you monster"])
    evs[0]["args"]["body"] = "spaces win, actually"
    _regenerate(evs)
    assert S.verify_chain(evs) == []   # proves the anchor is the ONLY thing that catches regeneration


def test_anchored_tip_on_empty_chain_is_a_mismatch():
    assert S.verify_chain([], expected_tip="a" * 64)   # nothing can't match a published tip
