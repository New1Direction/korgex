"""Ed25519-over-tip: turning "who" from an unsigned claim into a signature.

The audit's frontier: today an agent is an unsigned name string anyone can write.
This binds a history to a KEY — the agent *is* its public key (did:key style, like
SSH/Nostr/Bitcoin), no registry. A signer signs the chain's TIP from a sidecar
(off-chain, so the byte-identical conformance vectors are untouched). Anyone — in
their own browser — verifies the signature against the pubkey with zero trust in us.

What this proves: "the holder of key P attests to this exact history." What it does
NOT prove (named here, not hidden): that P belongs to a real-world entity (that needs
a public pin the relying party chooses, not a CA we run), and it doesn't stop a clone
from re-signing a copied history under a fresh key — only an *earliest external anchor*
distinguishes the original. Key-continuity + provenance, not legal identity.
"""
import json
import shutil
import subprocess

import pytest

from src import bus
from src import ledger_spec as S
from src import signing as SG


def _events(p):
    return [json.loads(ln) for ln in open(p) if ln.strip()]


def test_keypair_is_two_32_byte_hex_keys():
    priv, pub = SG.generate_keypair()
    assert len(priv) == 64 and len(pub) == 64
    assert SG.public_of(priv) == pub              # the public key is derivable from the private


def test_sign_then_verify_round_trips():
    priv, pub = SG.generate_keypair()
    tip = "ab" * 32
    assert SG.verify_tip(pub, tip, SG.sign_tip(priv, tip)) is True


def test_a_different_key_does_not_verify():
    priv, _ = SG.generate_keypair()
    _, other = SG.generate_keypair()
    assert SG.verify_tip(other, "ab" * 32, SG.sign_tip(priv, "ab" * 32)) is False


def test_a_tampered_tip_does_not_verify():
    priv, pub = SG.generate_keypair()
    assert SG.verify_tip(pub, "cd" * 32, SG.sign_tip(priv, "ab" * 32)) is False


def test_ed25519_signatures_are_deterministic():
    priv, _ = SG.generate_keypair()
    assert SG.sign_tip(priv, "ab" * 32) == SG.sign_tip(priv, "ab" * 32)   # frozen vectors possible


def test_a_checkpoint_signs_the_journal_tip(tmp_path):
    j = str(tmp_path / "x.jsonl")
    bus.send(j, "alice", "bob", "the deal is done")
    priv, pub = SG.generate_keypair()
    cp = SG.checkpoint(j, priv)
    v = SG.verify_checkpoint(j, cp)
    assert v["chain_ok"] and v["tip_match"] and v["sig_ok"]
    assert v["signer"] == pub


def test_verify_checkpoint_rejects_a_tampered_journal(tmp_path):
    j = str(tmp_path / "x.jsonl")
    bus.send(j, "alice", "bob", "original")
    priv, _ = SG.generate_keypair()
    cp = SG.checkpoint(j, priv)
    # forge the journal on disk
    evs = _events(j)
    evs[0]["args"]["body"] = "forged"
    with open(j, "w") as f:
        for e in evs:
            f.write(json.dumps(e) + "\n")
    v = SG.verify_checkpoint(j, cp)
    assert not (v["chain_ok"] and v["tip_match"])   # the signed tip no longer matches the forged chain


def test_two_signers_are_distinguishable_on_the_same_chain(tmp_path):
    """The 'who': two keys signing the same history produce two distinct, verifiable
    signers — you can tell agents apart without any registry."""
    j = str(tmp_path / "x.jsonl")
    bus.send(j, "alice", "bob", "hi")
    pa, ua = SG.generate_keypair()
    pb, ub = SG.generate_keypair()
    cpa, cpb = SG.checkpoint(j, pa), SG.checkpoint(j, pb)
    assert cpa["pubkey"] == ua and cpb["pubkey"] == ub and ua != ub
    assert SG.verify_checkpoint(j, cpa)["sig_ok"] and SG.verify_checkpoint(j, cpb)["sig_ok"]
    # and a signer can't be impersonated: A's signature doesn't verify under B's key
    assert SG.verify_tip(ub, cpa["tip"], cpa["sig"]) is False


def test_a_browser_can_verify_who_via_webcrypto():
    """Anyone re-verifies 'who' in their own browser (WebCrypto Ed25519), no trust in us."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to exercise the in-browser signature verify")
    priv, pub = SG.generate_keypair()
    tip = "ab" * 32
    sig = SG.sign_tip(priv, tip)
    driver = (
        "(async()=>{"
        f"const pub=Buffer.from({json.dumps(pub)},'hex');"
        "const key=await crypto.subtle.importKey('raw',pub,{name:'Ed25519'},false,['verify']);"
        f"const ok=await crypto.subtle.verify('Ed25519',key,Buffer.from({json.dumps(sig)},'hex'),Buffer.from({json.dumps(tip)},'hex'));"
        "process.stdout.write(ok?'OK':'BAD');})();"
    )
    out = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "OK"           # the Python signature verifies in the browser
