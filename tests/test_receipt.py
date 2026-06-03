"""Verifiable receipt — a portable, optionally-signed, self-verifying proof of a run.

A receipt embeds the ledger events (so it verifies offline, with no access to the
original journal), the chain tip, a human claim, a summary of what was done, and an
optional Ed25519 signature over the tip (authorship). Anyone can check it — offline
via verify_receipt, or in any browser via the self-verifying HTML — with zero trust
in korgex.
"""
from src import receipt as R
from src import korg_ledger as KL
from src import signing


def _chain(tmp_path):
    """A real hash-chained journal (not hand-rolled), so verify exercises the
    genuine korg-ledger@v1 chain rather than a fixture that could drift from it."""
    jp = str(tmp_path / "journal.jsonl")
    c = KL.LocalJournalClient(journal_path=jp)
    root = c.record_user_prompt("add a /healthz endpoint")
    c.record_llm_call("gpt-4o", 120, 60, 200, triggered_by=root)
    c.record_tool_call("Edit", {"file_path": "src/app.py"}, {"ok": True}, True, 12, triggered_by=root)
    return KL.load_journal_raw(jp)


# ── summary ───────────────────────────────────────────────────────────────────

def test_summarize_counts_files_and_cost(tmp_path):
    s = R.summarize(_chain(tmp_path))
    assert s["prompts"] == 1
    assert s["tool_calls"] >= 1
    assert "src/app.py" in s["files"]
    assert isinstance(s["cost_usd"], float) and s["cost_usd"] >= 0


# ── build ─────────────────────────────────────────────────────────────────────

def test_build_receipt_carries_tip_claim_and_events(tmp_path):
    events = _chain(tmp_path)
    rec = R.build_receipt(events, claim="shipped the /healthz endpoint", generated_at=1.0)
    assert rec["schema"] == "korgex-receipt@v1"
    assert rec["tip"] == events[-1]["entry_hash"]
    assert rec["event_count"] == len(events)
    assert rec["claim"] == "shipped the /healthz endpoint"
    assert rec["events"] == events                      # embedded → verifiable offline
    assert "signature" not in rec                       # unsigned unless a key is given


def test_build_receipt_signs_the_tip_when_key_given(tmp_path):
    events = _chain(tmp_path)
    priv, pub = signing.generate_keypair()
    rec = R.build_receipt(events, signer_priv=priv, generated_at=1.0)
    sig = rec["signature"]
    assert sig["alg"] == "ed25519" and sig["pubkey"] == pub
    assert signing.verify_tip(pub, rec["tip"], sig["sig"]) is True


# ── verify ──────────────────────────────────────────────────────────────────--

def test_verify_intact_receipt(tmp_path):
    v = R.verify_receipt(R.build_receipt(_chain(tmp_path), generated_at=1.0))
    assert v["ok"] is True
    assert v["chain_ok"] is True and v["tip_ok"] is True
    assert v["signature_ok"] is None                    # unsigned → not applicable


def test_verify_detects_event_tampering(tmp_path):
    rec = R.build_receipt(_chain(tmp_path), generated_at=1.0)
    rec["events"][1]["args"] = {"file_path": "EVIL"}    # edit a recorded event, leave its hash
    v = R.verify_receipt(rec)
    assert v["ok"] is False and v["chain_ok"] is False


def test_verify_detects_tip_swap(tmp_path):
    rec = R.build_receipt(_chain(tmp_path), generated_at=1.0)
    rec["tip"] = "0" * 64                               # claim a different head than the chain has
    v = R.verify_receipt(rec)
    assert v["tip_ok"] is False and v["ok"] is False


def test_verify_signed_receipt_ok_and_rejects_bad_signature(tmp_path):
    priv, _ = signing.generate_keypair()
    rec = R.build_receipt(_chain(tmp_path), signer_priv=priv, generated_at=1.0)
    assert R.verify_receipt(rec)["signature_ok"] is True

    rec["signature"]["sig"] = "00" * 64                 # forge the signature
    bad = R.verify_receipt(rec)
    assert bad["signature_ok"] is False and bad["ok"] is False


# ── shareable HTML (reuses the audit_report in-browser verifier) ───────────────

def test_render_html_is_self_contained_and_shows_the_claim(tmp_path):
    rec = R.build_receipt(_chain(tmp_path), claim="shipped the /healthz endpoint", generated_at=1.0)
    html = R.render_html(rec)
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "verifyChain" in html                        # the embedded in-browser verifier
    assert "shipped the /healthz endpoint" in html      # the claim is surfaced
    assert "<script src=" not in html                   # no network calls — an audit artifact must not phone home


# ── signing identity (persisted, so authorship is continuous across runs) ──────

def test_identity_prefers_env():
    assert R.load_or_create_identity(env={"KORGEX_SIGNING_KEY": "ab" * 32}) == "ab" * 32


def test_identity_created_then_reused(tmp_path):
    p = str(tmp_path / "id.key")
    k1 = R.load_or_create_identity(p, env={})
    assert len(k1) == 64 and signing.public_of(k1)      # a usable Ed25519 private key
    assert R.load_or_create_identity(p, env={}) == k1   # persisted → same identity next run


def test_identity_no_create_returns_none(tmp_path):
    assert R.load_or_create_identity(str(tmp_path / "missing.key"), env={}, create=False) is None
