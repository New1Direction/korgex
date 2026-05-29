#!/usr/bin/env python3
"""
Regenerate the korg-ledger@v1 conformance vectors from the reference impl.

Run from the repo root:  python3 spec/korg-ledger-v1/_generate_vectors.py

Produces vectors/*.jsonl + conformance.json. The frozen tip_entry_hash values
are the cross-language oracle: any conforming implementation (the Rust core in
idea #2, a JS verifier, etc.) MUST reproduce them byte-for-byte. Vectors are
fully deterministic — no timestamps, no randomness — so regeneration is stable.
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from src import ledger_spec as S  # noqa: E402

HERE = os.path.dirname(__file__)
VEC = os.path.join(HERE, "vectors")
HMAC_KEY = "korg-conformance-key"

# Deterministic base events — representative of a real korgex run, no clocks/RNG.
BASE = [
    {"schema_version": "1.0", "seq_id": 1, "source_agent": "agent:conformance",
     "tool_name": "user_prompt", "args": {"prompt": "add a function"},
     "result": {}, "success": True, "duration_ms": 0},
    {"schema_version": "1.0", "seq_id": 2, "source_agent": "agent:conformance",
     "tool_name": "llm_inference", "args": {"model": "m", "prompt_tokens": 10},
     "result": {"completion_tokens": 4}, "success": True, "duration_ms": 12, "triggered_by": 1},
    {"schema_version": "1.0", "seq_id": 3, "source_agent": "agent:conformance",
     "tool_name": "Write", "args": {"path": "mathx.py"},
     "result": {"ok": True}, "success": True, "duration_ms": 3, "triggered_by": 2},
]


def chain(events, key=None):
    """Stamp prev_hash/entry_hash onto a fresh copy of the events."""
    out, prev = [], S.GENESIS_HASH
    for e in events:
        e = dict(e, prev_hash=prev)
        e["entry_hash"] = S.chain_hash(e, key=key)
        prev = e["entry_hash"]
        out.append(e)
    return out


def write_jsonl(name, events):
    with open(os.path.join(VEC, name), "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def main():
    os.makedirs(VEC, exist_ok=True)
    key = HMAC_KEY.encode()

    basic = chain(BASE)
    hmacv = chain(BASE, key=key)

    # tampered: edit event 2's content, keep its (now-stale) entry_hash
    tcontent = [dict(e) for e in basic]
    tcontent[1] = dict(tcontent[1], args={"model": "EVIL", "prompt_tokens": 10})

    # tampered: delete the middle event (breaks event 3's prev_hash link)
    tdelete = [dict(basic[0]), dict(basic[2])]

    write_jsonl("basic-intact.jsonl", basic)
    write_jsonl("hmac-intact.jsonl", hmacv)
    write_jsonl("tampered-content.jsonl", tcontent)
    write_jsonl("tampered-deletion.jsonl", tdelete)

    manifest = {
        "spec_version": S.SPEC_VERSION,
        "canonicalization": "JSON, keys sorted by code point, separators (',',':'), "
                            "non-ASCII \\uXXXX-escaped; preimage = event minus entry_hash",
        "hmac": "HMAC-SHA256 over the same preimage when a key is present",
        "vectors": [
            {"file": "basic-intact.jsonl", "key": None, "verify": "intact",
             "tip_entry_hash": basic[-1]["entry_hash"]},
            {"file": "hmac-intact.jsonl", "key": HMAC_KEY, "verify": "intact",
             "tip_entry_hash": hmacv[-1]["entry_hash"]},
            {"file": "tampered-content.jsonl", "key": None, "verify": "tampered",
             "error_contains": "seq 2"},
            {"file": "tampered-deletion.jsonl", "key": None, "verify": "tampered",
             "error_contains": "seq 3"},
        ],
    }
    with open(os.path.join(HERE, "conformance.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print("wrote vectors + conformance.json")
    print("  basic tip:", basic[-1]["entry_hash"])
    print("  hmac  tip:", hmacv[-1]["entry_hash"])


if __name__ == "__main__":
    main()
