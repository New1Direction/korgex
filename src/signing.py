"""Ed25519-over-tip — per-party signatures that make "who" verifiable.

An agent's identity is its Ed25519 **public key** (did:key style — like SSH, Nostr,
Bitcoin). To claim a history, the holder of the private key signs the chain's **tip**
hash. The signature lives in a sidecar checkpoint ``{pubkey, tip, sig}``, OFF the
hashed chain, so the cross-language byte-identical conformance vectors are untouched —
the signature vouches for the chain from outside it.

Anyone verifies the signature against the pubkey, in their own browser, with zero
trust in korg (``verifyTipSig`` in assets/korg_verify.js, via WebCrypto Ed25519).

Honest boundary, stated not hidden:
  * This proves *the holder of key P attests to this exact history* (author-authenticity
    + key-continuity). It does NOT prove P belongs to a named real-world entity — that
    needs a public pin the relying party chooses (a signed post, a DNS record, a git
    identity), never a registry korg runs.
  * It does not stop a clone re-signing a copied history under a fresh key. Only an
    *earliest external anchor* (see ledger_spec.verify_chain expected_tip + a timestamp)
    distinguishes the original by provenance-in-time.
"""
from __future__ import annotations

import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from src import ledger_spec as S

_RAW = serialization.Encoding.Raw
_PRIV_RAW = serialization.PrivateFormat.Raw
_PUB_RAW = serialization.PublicFormat.Raw
_NOENC = serialization.NoEncryption()


def generate_keypair() -> tuple[str, str]:
    """A new agent identity. Returns ``(private_key_hex, public_key_hex)`` — 32 bytes each.
    The public key IS the identity; guard the private key like a wallet."""
    sk = ed25519.Ed25519PrivateKey.generate()
    priv = sk.private_bytes(_RAW, _PRIV_RAW, _NOENC).hex()
    pub = sk.public_key().public_bytes(_RAW, _PUB_RAW).hex()
    return priv, pub


def public_of(priv_hex: str) -> str:
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    return sk.public_key().public_bytes(_RAW, _PUB_RAW).hex()


def sign_tip(priv_hex: str, tip_hex: str) -> str:
    """Sign a chain tip hash. Ed25519 is deterministic, so (key, tip) → a fixed signature."""
    sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    return sk.sign(bytes.fromhex(tip_hex)).hex()


def verify_tip(pub_hex: str, tip_hex: str, sig_hex: str) -> bool:
    """True iff ``sig`` is a valid signature of ``tip`` by the holder of ``pub``."""
    try:
        pk = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pk.verify(bytes.fromhex(sig_hex), bytes.fromhex(tip_hex))
        return True
    except Exception:
        return False


def _events(journal_path: str) -> list:
    if not os.path.exists(journal_path):
        return []
    with open(journal_path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def checkpoint(journal_path: str, priv_hex: str) -> dict:
    """Sign the journal's current tip. Returns ``{pubkey, tip, sig}`` — portable proof that
    the holder of this key attests to the chain ending at this tip."""
    events = _events(journal_path)
    tip = events[-1]["entry_hash"] if events else S.GENESIS_HASH
    return {"pubkey": public_of(priv_hex), "tip": tip, "sig": sign_tip(priv_hex, tip)}


def verify_checkpoint(journal_path: str, cp: dict) -> dict:
    """Verify a signed checkpoint against a journal: the chain is intact, ends at the
    signed tip, and the signature is valid for that tip under the named key."""
    events = _events(journal_path)
    actual_tip = events[-1]["entry_hash"] if events else None
    return {
        "chain_ok": S.verify_chain(events) == [],
        "tip_match": actual_tip == cp["tip"],
        "sig_ok": verify_tip(cp["pubkey"], cp["tip"], cp["sig"]),
        "signer": cp["pubkey"],
    }
