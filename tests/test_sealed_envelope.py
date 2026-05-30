"""The sealed-envelope (commit-reveal) primitive — extracted from the demos into a
real, reusable korgex module. Seal a value before; reveal it after; anyone recomputes
the commit and proves the reveal is byte-identical to what was sealed. This is the
foundation every roadmap receipt (forecast, deliverable, custody) is built on.

Honest gotchas the audit flagged, pinned here:
- the auto-generated salt must carry real entropy (a 2-dp probability is ~100 values,
  so the commit must not be brute-forceable);
- the commit is sha256 over the SAME canonical encoding the JS verifier uses, so a
  reveal recomputes identically in the browser.
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src import ledger_spec as S
from src import sealed_envelope as SE

JS_ASSET = Path(__file__).resolve().parent.parent / "src" / "assets" / "korg_verify.js"


def test_seal_then_verify_round_trips():
    commit, salt = SE.seal("85% YES")
    assert SE.verify("85% YES", salt, commit) is True


def test_a_changed_value_fails():
    commit, salt = SE.seal("85% YES")
    assert SE.verify("10% YES", salt, commit) is False        # can't claim a different call


def test_a_changed_salt_fails():
    commit, salt = SE.seal("85% YES")
    assert SE.verify("85% YES", "not-the-salt", commit) is False


def test_commit_is_deterministic_given_value_and_salt():
    assert SE.commit_for("ship it", "abc123") == SE.commit_for("ship it", "abc123")


def test_seal_generates_a_fresh_high_entropy_salt_each_time():
    c1, s1 = SE.seal("yes")
    c2, s2 = SE.seal("yes")
    assert s1 != s2                       # fresh salt each seal
    assert len(s1) >= 32                  # >= 16 bytes hex → not brute-forceable
    assert c1 != c2                       # same value, different commit → the value stays hidden


def test_commit_is_canonical_sha256_over_the_spec_encoding():
    # ties the seal to korg-ledger@v1 canonicalization, so JS reproduces it byte-for-byte
    v, salt = "K♠ K♥", "deadbeef"        # non-ASCII to exercise the escape path
    expected = hashlib.sha256(S.canonicalize({"payload": v, "salt": salt})).hexdigest()
    assert SE.commit_for(v, salt) == expected


def test_payload_can_be_structured_not_just_a_string():
    payload = {"p": "0.85", "why": "kings on board"}
    commit, salt = SE.seal(payload)
    assert SE.verify(payload, salt, commit) is True
    assert SE.verify({"p": "0.10", "why": "kings on board"}, salt, commit) is False


def test_js_sealcommit_matches_python_byte_for_byte():
    """The browser-side reveal recompute must equal the Python commit, byte-for-byte —
    that's the whole 'verify it yourself' guarantee for sealed receipts."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to exercise the in-browser seal recompute")
    payload, salt = {"p": "0.85", "why": "kings on board ♠"}, "deadbeef00ff"
    py = SE.commit_for(payload, salt)
    driver = (
        f"const v=require({json.dumps(str(JS_ASSET))});"
        f"(async()=>{{process.stdout.write(await v.sealCommit({json.dumps(payload)},{json.dumps(salt)}));}})();"
    )
    out = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == py
