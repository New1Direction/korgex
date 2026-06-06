#!/usr/bin/env python3
"""CI gate: verify korg receipts / ledger journals — fail the build if the hash-chain
doesn't verify.

Resolves a verifier and runs it on every matched file:
  - ``KORG_VERIFY_BIN`` (env) — use this binary directly (also how this script is
    tested locally without a network install);
  - ``verifier: npx``  — ``npx @korgg/ledger-verify`` (the JS implementation);
  - ``verifier: cargo`` (default) — ``korg-verify`` from crates.io (installed if absent).

All three implementations emit the SAME ``--json`` verdict shape, so parsing is
uniform. Exit 0 = every file valid · 1 = at least one invalid · 2 = setup error.

Inputs arrive as ``INPUT_*`` env vars (GitHub composite-action convention).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys


def _bool(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _fail_setup(msg: str) -> None:
    print(f"::error::{msg}")
    sys.exit(2)


def resolve_verifier() -> list[str]:
    """The verifier argv prefix (file + flags are appended per call)."""
    override = os.environ.get("KORG_VERIFY_BIN")
    if override:
        return [override]
    verifier = os.environ.get("INPUT_VERIFIER", "cargo").strip().lower()
    if verifier == "npx":
        return ["npx", "--yes", "@korgg/ledger-verify"]
    # cargo (default): the published korg-verify binary
    if not shutil.which("korg-verify"):
        print("Installing korg-verify from crates.io…", flush=True)
        r = subprocess.run(["cargo", "install", "korg-verify"], capture_output=True, text=True)
        if r.returncode != 0:
            _fail_setup(f"`cargo install korg-verify` failed:\n{r.stderr[-2000:]}")
    return ["korg-verify"]


def write_summary(rows: list[tuple[str, str, str]], run: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path or not _bool("INPUT_SUMMARY"):
        return
    lines = [
        "### 🔗 korg ledger verification",
        "",
        f"Verifier: `{' '.join(run)}` — three independent implementations reproduce the "
        "same hashes, so a green check needs no trust in the tool that produced the ledger.",
        "",
        "| file | result | detail |",
        "|---|---|---|",
    ]
    lines += [f"| `{f}` | {res} | {detail} |" for f, res, detail in rows]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> None:
    glob_pat = os.environ.get("INPUT_PATH", ".korg/journal.json").strip()
    pubkey = os.environ.get("INPUT_PUBKEY", "").strip()
    run = resolve_verifier()

    files = sorted(glob.glob(glob_pat, recursive=True))
    if not files:
        # A gate pointed at a ledger that isn't there is a misconfig or a missing
        # receipt — fail loudly rather than silently "pass" with nothing verified.
        _fail_setup(f"no korg receipt/journal matched: {glob_pat!r}")

    rows: list[tuple[str, str, str]] = []
    any_invalid = False
    for f in files:
        args = list(run) + [f, "--json"] + (["--pubkey", pubkey] if pubkey else [])
        p = subprocess.run(args, capture_output=True, text=True)
        try:
            v = json.loads(p.stdout)
        except (ValueError, TypeError):
            any_invalid = True
            rows.append((f, "❌ error", (p.stderr or p.stdout or "no verifier output").strip()[:200]))
            print(f"✗ {f} — verifier produced no parseable verdict")
            continue
        if v.get("valid") is True:
            sig = v.get("signer") or ""
            detail = f"{v.get('event_count', '?')} events"
            if v.get("dag_ok"):
                detail += " · DAG ok"
            if sig and v.get("signature_ok"):
                detail += f" · signed {sig[:12]}…"
            rows.append((f, "✅ valid", detail))
            print(f"✓ {f} — {detail}")
        else:
            any_invalid = True
            errs = "; ".join(v.get("errors", [])[:3]) or "verification failed"
            rows.append((f, "❌ invalid", errs))
            print(f"✗ {f} — {errs}")

    write_summary(rows, run)
    if any_invalid and _bool("INPUT_FAIL_ON_INVALID"):
        print("::error::korg verification failed — see the job summary.")
        sys.exit(1)
    print(f"All {len(files)} ledger artifact(s) verified.")
    sys.exit(0)


if __name__ == "__main__":
    main()
