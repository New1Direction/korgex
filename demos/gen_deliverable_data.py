"""Regenerate the sealed-deliverable demo's crypto data block (korg-deliverable.html).

Builds the real 7-event korg-ledger@v1 contract chain for codex's deliverable,
seals + signs it, and emits the exact JSON the page's `<script id="data">` needs —
then SELF-VERIFIES that the chain is intact, the seal matches the real work, a
swapped deliverable does NOT match, codex's signature verifies, and an impostor's
does NOT. If any of that were false the demo would be lying; the asserts make that
impossible to ship.

    python3 demos/gen_deliverable_data.py                 # print the data JSON (runs the checks)
    python3 demos/gen_deliverable_data.py --write a.html b.html   # patch the data block in those files

Keys + salt are derived deterministically from fixed, obviously-demo seeds, so the
output is reproducible run-to-run. The sealed DELIVERABLE is kept ASCII-only so the
browser's sealCommit() reproduces the commit hash byte-for-byte.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src import contract as C  # noqa: E402
from src import ledger_spec as S  # noqa: E402
from src import sealed_envelope as SE  # noqa: E402
from src import signing as SG  # noqa: E402

# --- the deal --------------------------------------------------------------
BUYER, SELLER = "korgex", "codex"
AMOUNT, ASSET = "250.00", "USDC"
TASK = ("write a 3-point briefing on the top 3 e-bike brands — "
        "price, range, and the catch, with a source for each")
CRITERIA = "covers all 3 brands · price, range, and a source for each"

# the real work codex seals (ASCII-only so the browser recomputes the seal exactly)
DELIVERABLE = (
    "1. Rad Power RadCity 5 - $1,499, ~50 mi range, heavy at 65 lb. [radpowerbikes.com]\n"
    "2. Aventon Level.2 - $1,799, ~60 mi range, no throttle in some modes. [aventon.com]\n"
    "3. Lectric XP 3.0 - $999, ~45 mi range, small 20-inch wheels. [lectricebikes.com]"
)
# what a cheating codex tries to open AFTER sealing the real briefing (the "swap")
SWAPPED = (
    "1. Rad Power - great bike, just buy it.\n"
    "2. Aventon - also really good.\n"
    "3. Lectric - the cheap one."
)


def _priv(seed: str) -> str:
    """sha256(seed) is 32 bytes — a valid Ed25519 private seed. Clearly a demo key."""
    return hashlib.sha256(seed.encode()).hexdigest()


CODEX_PRIV = _priv("korg-demo-codex-key-v1")
KORGEX_PRIV = _priv("korg-demo-korgex-key-v1")
FORGER_PRIV = _priv("korg-demo-impostor-key-v1")
SALT = hashlib.sha256(b"korg-demo-deliverable-salt-v1").hexdigest()[:32]  # 16-byte hex


def build() -> dict:
    """Construct the chain, seal + sign, self-verify, and return the page's data dict."""
    j = str(_ROOT / "demos" / ".deliverable_gen.jsonl")
    if os.path.exists(j):
        os.remove(j)
    offer_seq = C.offer(j, BUYER, SELLER, TASK, CRITERIA)
    C.accept(j, SELLER, offer_seq)
    C.fund(j, BUYER, SELLER, AMOUNT, asset=ASSET, sign_with=KORGEX_PRIV)
    commit_seq, salt = C.commit(j, SELLER, DELIVERABLE, salt=SALT, sign_with=CODEX_PRIV)
    C.mark_deadline(j, BUYER)
    C.reveal(j, SELLER, commit_seq, DELIVERABLE, salt)
    C.record_test(j, BUYER, commit_seq, True, "3/3 passed")

    events = [json.loads(ln) for ln in open(j) if ln.strip()]
    os.remove(j)

    commit_hash = SE.commit_for(DELIVERABLE, salt)
    codex_pub = SG.public_of(CODEX_PRIV)
    codex_sig = SG.sign_tip(CODEX_PRIV, commit_hash)
    forger_sig = SG.sign_tip(FORGER_PRIV, commit_hash)
    tip = events[-1]["entry_hash"]

    # --- the demo must tell the truth -------------------------------------
    assert S.verify_chain(events) == [], "chain must be intact"
    assert S.verify_chain(events, expected_tip=tip) == [], "chain must anchor to its tip"
    assert SE.verify(DELIVERABLE, salt, commit_hash), "seal must match the real work"
    assert not SE.verify(SWAPPED, salt, commit_hash), "swapped work must NOT match the seal"
    assert SG.verify_tip(codex_pub, commit_hash, codex_sig), "codex's signature must verify"
    assert not SG.verify_tip(codex_pub, commit_hash, forger_sig), "impostor's sig must NOT verify"
    assert events[3]["args"]["commit"] == commit_hash, "commit event must carry the seal"
    assert events[3]["args"]["sig"] == codex_sig, "commit event must carry codex's signature"

    return {
        "buyer": BUYER, "seller": SELLER, "task": TASK, "criteria": CRITERIA,
        "amount": AMOUNT, "asset": ASSET,
        "deliverable": DELIVERABLE, "swapped": SWAPPED, "salt": salt, "commit": commit_hash,
        "codexPub": codex_pub, "codexSig": codex_sig, "forgerSig": forger_sig,
        "tip": tip, "events": events,
    }


def patch(html_path: Path, payload: str) -> None:
    """Replace the page's `<script id="data">…</script>` body with `payload`."""
    html = html_path.read_text()
    new = re.sub(r'(<script id="data" type="application/json">).*?(</script>)',
                 lambda m: m.group(1) + payload + m.group(2), html, count=1, flags=re.DOTALL)
    if new == html:
        raise SystemExit(f"data block not found in {html_path}")
    html_path.write_text(new)


if __name__ == "__main__":
    data = build()
    payload = json.dumps(data, ensure_ascii=False)
    targets = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--write" in sys.argv and targets:
        for t in targets:
            patch(Path(t), payload)
            print(f"patched data block in {t}", file=sys.stderr)
    else:
        print(payload)
