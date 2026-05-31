"""Proof-of-Custody — the first NON-agent receipt: seal any file, prove it's unaltered.

The expansion from agent↔agent trust to "proof of real" for anyone. Seal a file's
fingerprint (sha256) on the chain; later, re-hash the file in your own browser and
confirm it's byte-identical to what was sealed — with the custodian's signature for
WHO sealed it. The file's bytes never need to be stored; only its hash.

Honest boundary, pinned in tests: this proves the FILE is unaltered since the sealed
(and externally-anchored) moment, by a known key. It does NOT prove the content is
true, nor that a camera captured reality — that is hardware attestation / C2PA's job.
Bytes + time + author, not reality.
"""
import hashlib
import json
import shutil
import subprocess

import pytest

from src import custody as CU
from src import ledger_spec as S
from src import signing as SG

JS = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src" / "assets" / "korg_verify.js")


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _events(p):
    return [json.loads(ln) for ln in open(p) if ln.strip()]


def test_seal_then_verify_a_file(tmp_path):
    j = str(tmp_path / "c.jsonl")
    h = _h(b"the exact bytes of my report")
    CU.seal_file(j, h, label="q3-report.pdf")
    v = CU.verify_file(j, h)
    assert v["sealed"] is True and v["label"] == "q3-report.pdf"


def test_a_changed_file_is_not_the_sealed_one(tmp_path):
    j = str(tmp_path / "c.jsonl")
    CU.seal_file(j, _h(b"original bytes"), label="x")
    assert CU.verify_file(j, _h(b"one byte changed"))["sealed"] is False   # altered → not the sealed file


def test_a_signed_seal_proves_who_sealed_it(tmp_path):
    j = str(tmp_path / "c.jsonl")
    priv, pub = SG.generate_keypair()
    h = _h(b"evidence.bin")
    CU.seal_file(j, h, label="evidence.bin", sign_with=priv)
    assert CU.verify_file(j, h)["signed_by"] == pub                        # the custodian's key


def test_the_custody_record_is_a_valid_hash_chain(tmp_path):
    j = str(tmp_path / "c.jsonl")
    CU.seal_file(j, _h(b"a"), label="a")
    CU.seal_file(j, _h(b"b"), label="b")
    assert S.verify_chain(_events(j)) == []


def test_browser_hashes_a_file_identically(tmp_path):
    """The drag-in verify: the browser must hash the bytes to the same digest korg sealed."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    payload = "the exact bytes of my report"
    py = _h(payload.encode())
    driver = (
        f"const v=require({json.dumps(JS)});"
        f"(async()=>{{const buf=new TextEncoder().encode({json.dumps(payload)});"
        "process.stdout.write(await v.sha256Bytes(buf));})();"
    )
    out = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == py
