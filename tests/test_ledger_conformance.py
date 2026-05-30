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


# ── non-BMP / surrogate-pair conformance (the silent-divergence trap) ───────
# The ASCII BASE vectors never exercise the surrogate-pair code path. A U+10000+
# codepoint canonicalizes to a UTF-16 surrogate pair (\udXXX\udXXX), lower-case
# hex, which is exactly where a hand-written Rust/JS canonicalizer most easily
# diverges from Python's json.dumps. These tests pin that contract.

def test_nonbmp_vector_is_registered():
    vecs = [v for v in _load_vectors() if v["file"] == "nonbmp-intact.jsonl"]
    assert vecs, "nonbmp-intact.jsonl must be registered in conformance.json"
    assert vecs[0]["verify"] == "intact"
    assert vecs[0].get("tip_entry_hash"), "non-BMP vector must carry a frozen tip"


def test_nonbmp_vector_file_is_ascii_only_on_disk():
    # The on-disk JSONL must itself be ASCII (\uXXXX-escaped) so the vector is
    # byte-stable and git-diffable across platforms — no raw UTF-8 in the file.
    raw = open(os.path.join(VECTORS_DIR, "nonbmp-intact.jsonl"), "rb").read()
    assert raw.isascii(), "non-BMP vector file must be ASCII-only on disk"
    # and it must actually contain a surrogate-pair escape (the trap it guards)
    assert b"\\ud83d\\ude00" in raw, "expected the U+1F600 surrogate pair in the vector"


def test_nonbmp_vector_verifies_and_reproduces_frozen_tip():
    vec = [v for v in _load_vectors() if v["file"] == "nonbmp-intact.jsonl"][0]
    events = _read_jsonl(vec["file"])
    errors = S.verify_chain(events) + S.verify_dag(events)
    assert errors == [], f"non-BMP vector expected intact, got {errors}"
    # ledger_spec MUST reproduce the frozen tip byte-for-byte over surrogate pairs
    assert events[-1]["entry_hash"] == vec["tip_entry_hash"], \
        "non-BMP tip hash drifted — surrogate-pair canonicalization changed"


def test_surrogate_pair_canonicalization_is_pinned():
    # First principles: the astral codepoint U+1F600 MUST canonicalize to the
    # lower-case UTF-16 surrogate pair, not \u{1F600} and not raw UTF-8 bytes.
    # A Rust/JS port that gets this wrong fails the non-BMP vector above.
    assert S.canonicalize({"x": "\U0001F600"}) == b'{"x":"\\ud83d\\ude00"}'
    assert S.canonicalize({"x": "\U00010000"}) == b'{"x":"\\ud800\\udc00"}'
    assert S.canonicalize({"x": "中"}) == b'{"x":"\\u4e2d"}'
