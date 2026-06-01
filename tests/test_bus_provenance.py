"""Provenance-verified agent bus — per-message identity you can re-check.

A coordination channel where messages arrive as trusted turns is an
instruction-injection surface: anyone who can write can impersonate a peer. Most
multi-agent buses are "trust-flat". korgex's edge: bind an Ed25519 signature over
each message and RE-RESOLVE the sender's identity per message at receive time —
so a message's "who" is a signature, not a self-asserted string. These tests pin
the signing + per-message verification.
"""
from src import bus
from src import signing


def test_signed_message_verifies_under_its_key(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    priv, pub = signing.generate_keypair()
    bus.send(j, "alice", "bob", "ship the build", sign_with=priv)
    msg = bus.inbox(j, "bob")[0]
    # the message carries the sender's pubkey + a signature, and it verifies
    assert msg["pubkey"] == pub
    assert bus.verify_message(msg) is True


def test_tampered_body_fails_verification(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    priv, _ = signing.generate_keypair()
    bus.send(j, "alice", "bob", "transfer $10", sign_with=priv)
    msg = bus.inbox(j, "bob")[0]
    # an attacker rewrites the body but can't re-sign without alice's key
    forged = {**msg, "body": "transfer $10000"}
    assert bus.verify_message(forged) is False


def test_impersonation_rejected_against_expected_key(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    alice_priv, alice_pub = signing.generate_keypair()
    imposter_priv, _ = signing.generate_keypair()
    # someone sends AS "alice" but signs with the imposter's key
    bus.send(j, "alice", "bob", "approve the deploy", sign_with=imposter_priv)
    msg = bus.inbox(j, "bob")[0]
    # the signature is internally valid (verify_message True)…
    assert bus.verify_message(msg) is True
    # …but it does NOT match alice's KNOWN published key → impersonation caught
    assert bus.verify_message(msg, expected_pubkey=alice_pub) is False


def test_unsigned_message_is_unverified_not_crash(tmp_path):
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "alice", "bob", "fyi")  # no sign_with → legacy/unsigned
    msg = bus.inbox(j, "bob")[0]
    assert msg.get("pubkey") is None
    assert bus.verify_message(msg) is False  # no signature → not verified


def test_signing_is_optional_back_compat(tmp_path):
    """Unsigned sends still work end-to-end (existing callers unaffected)."""
    j = str(tmp_path / "bus.jsonl")
    bus.send(j, "a", "b", "hello")
    assert bus.inbox(j, "b")[0]["body"] == "hello"


def test_message_digest_is_stable_and_field_bound(tmp_path):
    # The signed digest binds from+to+body, so changing any of them changes it.
    d1 = bus.message_digest("alice", "bob", "hi")
    d2 = bus.message_digest("alice", "bob", "hi")
    assert d1 == d2 and len(d1) == 64  # sha256 hex
    assert bus.message_digest("alice", "carol", "hi") != d1   # 'to' bound
    assert bus.message_digest("eve", "bob", "hi") != d1       # 'from' bound
    assert bus.message_digest("alice", "bob", "bye") != d1    # 'body' bound


# ── integration: agent bus tools sign + surface verification ───────────────────

def test_agent_bus_send_signs_and_inbox_reports_verified(tmp_path, monkeypatch):
    from src import tools_impl, signing
    j = str(tmp_path / "bus.jsonl")
    priv, _ = signing.generate_keypair()
    # alice sends (signed) to bob
    monkeypatch.setenv("KORG_BUS_JOURNAL", j)
    monkeypatch.setenv("KORG_BUS_AGENT", "alice")
    monkeypatch.setenv("KORG_BUS_KEY", priv)
    out = tools_impl.tool_bus_send("bob", "deploy now")
    assert out["sent"] is True and out["signed"] is True
    # bob reads — the message shows verified=True
    monkeypatch.setenv("KORG_BUS_AGENT", "bob")
    monkeypatch.delenv("KORG_BUS_KEY", raising=False)
    inbox = tools_impl.tool_bus_inbox()
    assert inbox["messages"][0]["verified"] is True
    assert inbox["messages"][0]["body"] == "deploy now"


def test_agent_bus_unsigned_send_is_unverified(tmp_path, monkeypatch):
    from src import tools_impl
    j = str(tmp_path / "bus.jsonl")
    monkeypatch.setenv("KORG_BUS_JOURNAL", j)
    monkeypatch.setenv("KORG_BUS_AGENT", "alice")
    monkeypatch.delenv("KORG_BUS_KEY", raising=False)  # no identity → unsigned
    tools_impl.tool_bus_send("bob", "hi")
    monkeypatch.setenv("KORG_BUS_AGENT", "bob")
    inbox = tools_impl.tool_bus_inbox()
    assert inbox["messages"][0]["verified"] is False
