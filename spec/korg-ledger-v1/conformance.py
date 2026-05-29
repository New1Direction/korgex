#!/usr/bin/env python3
"""
korg-ledger@v1 conformance harness (Python reference).

Runs the frozen vectors against src/ledger_spec and reports PASS/FAIL; exits 0
iff the implementation is conformant. An implementation in another language
(e.g. the Rust core) ships its own harness that reads the SAME conformance.json
+ vectors/ and must produce the SAME results — including the frozen
tip_entry_hash on every intact vector.

    python3 spec/korg-ledger-v1/conformance.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
from src import ledger_spec as S  # noqa: E402


def _read_jsonl(name):
    with open(os.path.join(HERE, "vectors", name)) as f:
        return [json.loads(line) for line in f if line.strip()]


def run() -> int:
    with open(os.path.join(HERE, "conformance.json")) as f:
        manifest = json.load(f)
    assert manifest["spec_version"] == S.SPEC_VERSION, "version mismatch"

    failures = 0
    for v in manifest["vectors"]:
        events = _read_jsonl(v["file"])
        key = v["key"].encode() if v.get("key") else None
        errors = S.verify_chain(events, key=key) + S.verify_dag(events)
        ok = True
        detail = ""
        if v["verify"] == "intact":
            if errors:
                ok, detail = False, f"expected intact, got {errors}"
            elif events[-1].get("entry_hash") != v["tip_entry_hash"]:
                ok, detail = False, "tip_entry_hash drifted"
        else:
            if not errors:
                ok, detail = False, "expected tampered, verified clean"
            elif not any(v["error_contains"] in e for e in errors):
                ok, detail = False, f"errors {errors} missing {v['error_contains']!r}"
        print(f"  [{'PASS' if ok else 'FAIL'}] {v['file']:<26} {v['verify']:<8} {detail}")
        failures += 0 if ok else 1

    print(f"\nkorg-ledger@v1 conformance: {'PASS' if not failures else f'{failures} FAILURE(S)'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(run())
