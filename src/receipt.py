"""Verifiable receipt — a portable, optionally-signed, self-verifying proof of a run.

A receipt is a self-contained slice of the korg-ledger: the events (embedded, so it
verifies OFFLINE with no access to the original journal), the chain tip, a human
claim, a summary of what was done, and an optional Ed25519 signature over the tip
(authorship). Share one file — or its self-verifying HTML, which re-checks the hash
chain in the recipient's own browser — and anyone can confirm, with zero trust in
korgex, that this is exactly what the agent did and (if signed) who attests to it.

This is the consumer edge of the verifiable-cognition moat. What it proves: the
embedded chain is internally consistent and unedited (tamper-evident), and — if
signed — the holder of the named key attests to this exact tip. What it does NOT
prove on its own: *when* the work happened (needs an external time anchor) or that
the key maps to a named real-world entity (the relying party pins that out-of-band).
See signing.py for that honest boundary.
"""
from __future__ import annotations

import os
from pathlib import Path

from src import ledger_spec as S
from src import signing

SCHEMA = "korgex-receipt@v1"


def identity_path() -> str:
    """Where the agent's persistent Ed25519 identity lives (the public key IS the
    identity, so the same key across runs makes a continuous, attributable history)."""
    return os.path.join(os.path.expanduser("~"), ".korgex", "identity.key")


def load_or_create_identity(path: str | None = None, *, env=None, create: bool = True) -> str | None:
    """Return the signing private-key hex. Prefers ``KORGEX_SIGNING_KEY`` (key off-disk),
    else the saved file; creates + persists a new one (0600) on first use unless
    ``create=False``. Returns None when there's nothing and creation is disabled."""
    env = os.environ if env is None else env
    from_env = env.get("KORGEX_SIGNING_KEY")
    if from_env:
        return from_env.strip()
    p = Path(path or identity_path())
    if p.exists():
        return p.read_text().strip()
    if not create:
        return None
    priv, _ = signing.generate_keypair()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(priv)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return priv


def _kind(e: dict) -> str:
    return e.get("tool_name") or e.get("event_type") or ""


def summarize(events: list) -> dict:
    """A plain-language roll-up of what the run did: prompt / inference / tool-call
    counts, the distinct files touched, a per-tool tally, and the estimated USD cost."""
    from src.cost import estimate_cost

    prompts = inferences = tool_calls = 0
    files: list[str] = []
    by_tool: dict[str, int] = {}
    for e in events or []:
        k = _kind(e)
        if k in ("user_prompt", "user_message"):
            prompts += 1
        elif k == "llm_inference":
            inferences += 1
        else:
            tool_calls += 1
            by_tool[k] = by_tool.get(k, 0) + 1
            args = e.get("args") or {}
            f = args.get("file_path") or args.get("path") or args.get("notebook_path")
            if f and f not in files:
                files.append(f)
    cost = estimate_cost(events)
    return {
        "prompts": prompts,
        "inferences": inferences,
        "tool_calls": tool_calls,
        "files": files,
        "by_tool": by_tool,
        "cost_usd": round(float(cost.get("total_usd", 0.0)), 6),
    }


def build_receipt(events, *, claim=None, signer_priv=None, generated_at=None, meta=None) -> dict:
    """Assemble a portable receipt from ledger `events`. Signs the tip if `signer_priv`
    (an Ed25519 private-key hex) is given — the signature lives OFF the hashed chain, so
    the cross-language conformance vectors stay byte-identical (see signing.py)."""
    events = list(events or [])
    tip = events[-1].get("entry_hash") if events else S.GENESIS_HASH
    receipt = {
        "schema": SCHEMA,
        "spec": "korg-ledger@v1",
        "claim": claim,
        "generated_at": generated_at,
        "event_count": len(events),
        "tip": tip,
        "summary": summarize(events),
        "events": events,
    }
    if meta:
        receipt["meta"] = dict(meta)
    if signer_priv:
        receipt["signature"] = {
            "alg": "ed25519",
            "pubkey": signing.public_of(signer_priv),
            "sig": signing.sign_tip(signer_priv, tip),
        }
    return receipt


def verify_receipt(receipt: dict, *, key: bytes | None = None) -> dict:
    """Re-verify a receipt from its OWN embedded events — no original journal needed.
    Checks (1) the DAG + hash chain recompute intact, (2) the recorded tip matches the
    chain head, and (3) if signed, the signature verifies for that tip under the
    embedded public key. ``ok`` is the conjunction; ``signature_ok`` is None when the
    receipt is unsigned (not applicable — not a failure)."""
    events = receipt.get("events") or []
    chain_errs = S.verify_dag(events) + S.verify_chain(events, key=key)
    chain_ok = not chain_errs
    head = events[-1].get("entry_hash") if events else S.GENESIS_HASH
    tip_ok = head == receipt.get("tip")

    sig = receipt.get("signature") or {}
    signature_ok = None
    if sig:
        signature_ok = signing.verify_tip(
            sig.get("pubkey", ""), receipt.get("tip", ""), sig.get("sig", ""))

    errors = list(chain_errs)
    if not tip_ok:
        errors.append("recorded tip does not match the chain head")
    if signature_ok is False:
        errors.append("signature does not verify for the recorded tip")

    return {
        "ok": chain_ok and tip_ok and signature_ok in (None, True),
        "chain_ok": chain_ok,
        "tip_ok": tip_ok,
        "signature_ok": signature_ok,
        "signer": sig.get("pubkey"),
        "errors": errors,
    }


# A real, hosted, on-brand card image (resolves with image/* content-type). Override with
# KORGEX_SHARE_OG_IMAGE once a dedicated 1200×630 card is hosted on the share domain.
DEFAULT_OG_IMAGE = "https://raw.githubusercontent.com/New1Direction/Korgex/main/docs/images/banner.jpg"


def _share_description(receipt: dict) -> str:
    """A one-line social-card description, built from the receipt's own summary."""
    s = receipt.get("summary") or {}
    n = receipt.get("event_count", len(receipt.get("events") or []))
    bits = [f"{n} verifiable events"]
    files = s.get("files") or []
    if files:
        bits.append(f"{len(files)} file{'s' if len(files) != 1 else ''} touched")
    if s.get("cost_usd"):
        bits.append(f"~${s['cost_usd']:.2f}")
    signed = " · signed" if receipt.get("signature") else ""
    return f"Tamper-evident proof of an AI agent run — {' · '.join(bits)}{signed}. Re-verify it yourself, zero trust."


def render_html(receipt: dict, *, og_image: str | None = None, base_url: str | None = None) -> str:
    """Render the receipt as one self-contained, *shareable* HTML page that re-verifies the
    chain in the recipient's browser — reusing the conformance-tested audit_report verifier,
    so there's no second hash-chain implementation to keep in sync. Adds an Open Graph /
    Twitter card (a tweeted link unfurls), surfaces the signer, pins the signed tip for the
    in-browser check, and embeds the receipt with the exact pip/Rust commands to re-check it
    outside the browser. `base_url` is the page's public URL once hosted (og:url)."""
    from src import audit_report as AR

    claim = receipt.get("claim") or "korgex receipt"
    sig = receipt.get("signature") or {}
    meta = {
        "session": claim,
        "vendor": "korgex",
        "spec": receipt.get("spec", "korg-ledger@v1"),
        "anchored_tip": receipt.get("tip"),  # in-browser verifier also flags a fully-regenerated chain
        "share": {
            "title": claim,
            "description": _share_description(receipt),
            "image": og_image or DEFAULT_OG_IMAGE,
            "url": base_url,
        },
        "verify": {
            "pip": "pip install korgex && korgex receipt verify <receipt>.json",
            "cargo": "cargo install korg-verify && korg-verify <receipt>.json",
        },
        "receipt": receipt,
    }
    if sig:
        meta["signed_by"] = sig.get("pubkey")
    return AR.render_html(receipt.get("events") or [], meta)
