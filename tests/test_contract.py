"""Sealed Deliverable Receipt — the honest, buildable core of agent escrow.

Two agents strike a deal (offer → accept), the deliverer SEALS its work before a
deadline, REVEALS it after, an acceptance test is recorded, and the VERDICT —
SETTLED / DEFAULTED / FRAUD / FAILED — is a pure function of the hash-chained
record. korg proves *who delivered what, when, and whether it changed*; it does
NOT move money (that's an external rail) and "who" is a claim until per-party
signing (Ed25519). This is evidence an arbiter consults, not atomic escrow.
"""
from src import contract as C
from src import ledger_spec as S


def _events(p):
    import json
    return [json.loads(ln) for ln in open(p) if ln.strip()]


def test_happy_path_settles(tmp_path):
    j = str(tmp_path / "deal.jsonl")
    o = C.offer(j, "buyer", "seller", "write add(a,b)", "passes 3 tests")
    C.accept(j, "seller", o)
    cseq, salt = C.commit(j, "seller", "def add(a,b): return a+b")
    C.mark_deadline(j, "buyer")
    C.reveal(j, "seller", cseq, "def add(a,b): return a+b", salt)
    C.record_test(j, "buyer", cseq, True, "3/3 passed")
    assert C.verdict(j)["status"] == "SETTLED"


def test_no_commit_before_deadline_is_a_default(tmp_path):
    j = str(tmp_path / "deal.jsonl")
    o = C.offer(j, "buyer", "seller", "ship it", "tests pass")
    C.accept(j, "seller", o)
    C.mark_deadline(j, "buyer")            # deadline hits, nothing sealed
    assert C.verdict(j)["status"] == "DEFAULTED"


def test_committing_after_the_deadline_is_a_default(tmp_path):
    j = str(tmp_path / "deal.jsonl")
    o = C.offer(j, "buyer", "seller", "ship it", "tests pass")
    C.accept(j, "seller", o)
    C.mark_deadline(j, "buyer")
    C.commit(j, "seller", "too late")      # sealed after the deadline marker
    assert C.verdict(j)["status"] == "DEFAULTED"


def test_revealing_something_other_than_what_was_sealed_is_fraud(tmp_path):
    j = str(tmp_path / "deal.jsonl")
    o = C.offer(j, "buyer", "seller", "task", "criteria")
    C.accept(j, "seller", o)
    cseq, salt = C.commit(j, "seller", "the real (worse) answer")
    C.mark_deadline(j, "buyer")
    C.reveal(j, "seller", cseq, "a better answer I swapped in", salt)   # ≠ what was sealed
    v = C.verdict(j)
    assert v["status"] == "FRAUD"


def test_matching_reveal_that_fails_the_test_is_failed_not_fraud(tmp_path):
    j = str(tmp_path / "deal.jsonl")
    o = C.offer(j, "buyer", "seller", "task", "criteria")
    C.accept(j, "seller", o)
    cseq, salt = C.commit(j, "seller", "def add(a,b): return a-b")      # honest but wrong
    C.mark_deadline(j, "buyer")
    C.reveal(j, "seller", cseq, "def add(a,b): return a-b", salt)
    C.record_test(j, "buyer", cseq, False, "1/3 passed")
    assert C.verdict(j)["status"] == "FAILED"


def test_the_whole_deal_is_a_valid_hash_chain(tmp_path):
    j = str(tmp_path / "deal.jsonl")
    o = C.offer(j, "buyer", "seller", "t", "c")
    C.accept(j, "seller", o)
    cseq, salt = C.commit(j, "seller", "x")
    C.mark_deadline(j, "buyer")
    C.reveal(j, "seller", cseq, "x", salt)
    C.record_test(j, "buyer", cseq, True)
    assert S.verify_chain(_events(j)) == []        # the contract IS a tamper-evident record


def test_reveal_survives_secret_redaction(tmp_path):
    """The salt + deliverable must persist verbatim (the audit's redaction trap):
    redact() scrubs keys named secret/token/… and secret-shaped values, but a hex
    salt under key 'salt' and a plain deliverable must come through untouched —
    otherwise an honest reveal would read back as FRAUD."""
    j = str(tmp_path / "deal.jsonl")
    o = C.offer(j, "buyer", "seller", "t", "c")
    C.accept(j, "seller", o)
    cseq, salt = C.commit(j, "seller", "the deliverable text")
    C.reveal(j, "seller", cseq, "the deliverable text", salt)
    rev = [e for e in _events(j) if e["tool_name"] == C.REVEAL][0]["args"]
    assert rev["salt"] == salt and rev["deliverable"] == "the deliverable text"


def test_a_signed_commit_proves_authorship(tmp_path):
    """With a per-party key, the seal is SIGNED — 'who delivered' stops being a claim."""
    from src import signing as SG
    j = str(tmp_path / "deal.jsonl")
    priv, pub = SG.generate_keypair()
    o = C.offer(j, "korgex", "codex", "task", "criteria")
    C.accept(j, "codex", o)
    cseq, salt = C.commit(j, "codex", "the work", sign_with=priv)
    C.mark_deadline(j, "korgex")
    C.reveal(j, "codex", cseq, "the work", salt)
    C.record_test(j, "korgex", cseq, True)
    v = C.verdict(j)
    assert v["status"] == "SETTLED"
    assert v["signed_by"] == pub                  # codex's key provably sealed it


def test_an_unsigned_commit_has_no_proven_signer(tmp_path):
    j = str(tmp_path / "deal.jsonl")
    o = C.offer(j, "korgex", "codex", "task", "criteria")
    C.accept(j, "codex", o)
    cseq, salt = C.commit(j, "codex", "the work")
    C.mark_deadline(j, "korgex")
    C.reveal(j, "codex", cseq, "the work", salt)
    C.record_test(j, "korgex", cseq, True)
    assert C.verdict(j)["signed_by"] is None       # an unsigned name proves nothing about who


# ── escrow: the x402 payment leg, gated by the provable verdict ──
def _full_deal(j, deliverable="the work", reveal=None, passed=True, late=False):
    o = C.offer(j, "korgex", "codex", "task", "criteria")
    C.accept(j, "codex", o)
    if not late:
        cseq, salt = C.commit(j, "codex", deliverable)
    C.mark_deadline(j, "korgex")
    if late:
        cseq, salt = C.commit(j, "codex", deliverable)
    C.reveal(j, "codex", cseq, reveal if reveal is not None else deliverable, salt)
    C.record_test(j, "korgex", cseq, passed)


def test_fund_records_a_signed_x402_authorization(tmp_path):
    from src import signing as SG
    j = str(tmp_path / "e.jsonl")
    priv, pub = SG.generate_keypair()
    C.offer(j, "korgex", "codex", "t", "c")
    C.fund(j, "korgex", "codex", "250.00", sign_with=priv)
    f = [e for e in _events(j) if e["tool_name"] == C.FUND][0]["args"]
    assert f["payment"]["scheme"] == "x402" and f["payment"]["amount"] == "250.00"
    assert f["pubkey"] == pub                       # the buyer signed the payment authorization


def test_escrow_releases_to_seller_on_settled(tmp_path):
    j = str(tmp_path / "e.jsonl")
    C.fund(j, "korgex", "codex", "250.00")
    _full_deal(j)
    s = C.escrow_status(j)
    assert s["delivery"] == "SETTLED" and s["action"] == "release" and s["pays"] == "codex"


def test_escrow_refunds_to_buyer_on_fraud(tmp_path):
    j = str(tmp_path / "e.jsonl")
    C.fund(j, "korgex", "codex", "250.00")
    _full_deal(j, deliverable="real work", reveal="swapped work")
    s = C.escrow_status(j)
    assert s["delivery"] == "FRAUD" and s["action"] == "refund" and s["pays"] == "korgex"


def test_escrow_refunds_to_buyer_on_default(tmp_path):
    j = str(tmp_path / "e.jsonl")
    C.fund(j, "korgex", "codex", "250.00")
    o = C.offer(j, "korgex", "codex", "t", "c"); C.accept(j, "codex", o); C.mark_deadline(j, "korgex")
    s = C.escrow_status(j)
    assert s["delivery"] == "DEFAULTED" and s["action"] == "refund" and s["pays"] == "korgex"


def test_payment_authorization_survives_redaction(tmp_path):
    """The x402 payment dict must persist (the 'authorization' key would be scrubbed by
    redact() — we use 'payment'). Otherwise escrow_status can't read who/how-much."""
    j = str(tmp_path / "e.jsonl")
    C.fund(j, "korgex", "codex", "250.00")
    f = [e for e in _events(j) if e["tool_name"] == C.FUND][0]["args"]
    assert isinstance(f["payment"], dict) and f["payment"]["amount"] == "250.00"
