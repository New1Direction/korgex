"""
korg-ledger@v1 conformance tests.

Idea #1 of the ecosystem roadmap: the tamper-evident hash-chain stops being a
korgex feature and becomes a *frozen spec* with a dependency-free reference
implementation (`src/ledger_spec.py`) and language-agnostic conformance vectors
(`spec/korg-ledger-v1/`). korgex imports the reference instead of owning it; the
Rust core (idea #2) must reproduce the SAME entry_hashes against the SAME
vectors. These tests are the oracle that makes that cross-language equivalence
checkable.

The vectors are deliberately anchored to first principles: one test recomputes
the genesis entry_hash as a plain SHA-256 of the canonical preimage, so the
frozen values are authoritative, not self-referential.
"""

import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import ledger_spec as S  # noqa: E402

SPEC_DIR = os.path.join(ROOT, "spec", "korg-ledger-v1")
VECTORS_DIR = os.path.join(SPEC_DIR, "vectors")
CONFORMANCE = os.path.join(SPEC_DIR, "conformance.json")


# ── the spec surface ────────────────────────────────────────────────────────

def test_spec_module_exports_v1_surface():
    assert S.SPEC_VERSION == "korg-ledger@v1"
    assert S.GENESIS_HASH == "0" * 64
    for fn in ("canonicalize", "chain_hash", "verify_chain", "verify_dag"):
        assert callable(getattr(S, fn))


# ── canonicalization is exact + cross-language reproducible ─────────────────

def test_canonicalize_is_sorted_compact_ascii():
    # sorted keys, no insignificant whitespace, non-ASCII escaped (\uXXXX).
    # Pinning this is what lets a Rust impl reproduce byte-identical preimages.
    assert S.canonicalize({"b": 1, "a": "é"}) == b'{"a":"\\u00e9","b":1}'
    assert S.canonicalize({"z": [3, 2], "a": {"y": 1, "x": 2}}) == \
        b'{"a":{"x":2,"y":1},"z":[3,2]}'


def test_genesis_hash_is_plain_sha256_of_canonical_preimage():
    # Non-circular anchor: chain_hash MUST equal sha256 of the canonical event
    # with entry_hash removed. If this ever changes, the spec changed.
    ev = {"seq_id": 1, "tool_name": "user_prompt",
          "args": {"prompt": "hi"}, "prev_hash": S.GENESIS_HASH}
    preimage = {k: v for k, v in ev.items() if k != "entry_hash"}
    expected = hashlib.sha256(
        json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert S.chain_hash(ev) == expected


def test_entry_hash_excluded_from_its_own_preimage():
    ev = {"seq_id": 1, "tool_name": "x", "prev_hash": S.GENESIS_HASH}
    h = S.chain_hash(ev)
    assert S.chain_hash(dict(ev, entry_hash="anything")) == h


# ── korgex imports the spec; it does not own a second copy ──────────────────

def test_korg_ledger_reexports_the_reference():
    from src import korg_ledger as L
    assert L.chain_hash is S.chain_hash
    assert L.verify_chain is S.verify_chain
    assert L.verify_dag is S.verify_dag
    assert L.GENESIS_HASH == S.GENESIS_HASH


# ── golden conformance vectors (the cross-impl oracle) ──────────────────────

def _load_vectors():
    with open(CONFORMANCE) as f:
        spec = json.load(f)
    assert spec["spec_version"] == "korg-ledger@v1"
    return spec["vectors"]


def _read_jsonl(name):
    with open(os.path.join(VECTORS_DIR, name)) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_conformance_manifest_present():
    assert os.path.isfile(CONFORMANCE), "spec/korg-ledger-v1/conformance.json missing"
    assert _load_vectors(), "no conformance vectors declared"


@pytest.mark.parametrize("vec", _load_vectors() if os.path.isfile(CONFORMANCE) else [])
def test_vector_conforms(vec):
    events = _read_jsonl(vec["file"])
    key = vec["key"].encode() if vec.get("key") else None
    errors = S.verify_chain(events, key=key) + S.verify_dag(events)

    if vec["verify"] == "intact":
        assert errors == [], f"{vec['file']} expected intact, got {errors}"
        # the frozen chain tip — what the Rust port MUST reproduce byte-for-byte
        assert events[-1]["entry_hash"] == vec["tip_entry_hash"], \
            f"{vec['file']} tip hash drifted"
    else:
        assert errors, f"{vec['file']} expected tampered, but verified clean"
        assert any(vec["error_contains"] in e for e in errors), \
            f"{vec['file']} errors {errors} missing {vec['error_contains']!r}"


def test_hmac_vector_fails_without_the_key():
    # A keyed vector verified with NO key must fail — proves tamper-PROOF mode.
    hmac_vecs = [v for v in _load_vectors() if v.get("key") and v["verify"] == "intact"]
    assert hmac_vecs, "expected at least one HMAC conformance vector"
    events = _read_jsonl(hmac_vecs[0]["file"])
    assert S.verify_chain(events, key=None), "keyed chain wrongly verified without the key"
