"""Sealed Deliverable Receipt — provable agent deals on korg-ledger@v1.

The honest, buildable core of "agent escrow": two agents agree (offer → accept),
the deliverer SEALS its work before a deadline (commit), REVEALS it after, an
acceptance test is recorded, and the VERDICT is a *pure function of the chain*:

    SETTLED   – sealed before the deadline, reveal matches the seal, passed acceptance
    DEFAULTED – nothing sealed, or sealed only after the deadline marker
    FRAUD     – the revealed deliverable doesn't match what was sealed
    FAILED    – matches the seal but doesn't pass the acceptance criteria
    PENDING/REVEALED – mid-flight

What this proves: who delivered what, in what order, by the (in-journal) deadline,
and whether the reveal is byte-identical to the seal — a tamper-evident record an
arbiter consults. What it deliberately does NOT do: move money atomically (that is
an external value rail), enforce a remedy (an arbiter does), or bind "seller" to a
real party — `from`/`source_agent` are unsigned strings until Ed25519-over-tip
lands. The deadline here is the chain ORDER of a `contract.deadline` event; a real
wall-clock deadline needs the commit's tip anchored to an external clock.
"""
from __future__ import annotations

import json
import os

from src import sealed_envelope as SE
from src import signing as SG
from src.korg_ledger import LocalJournalClient

OFFER = "contract.offer"
ACCEPT = "contract.accept"
COMMIT = "contract.commit"
DEADLINE = "contract.deadline"
REVEAL = "contract.reveal"
TEST = "contract.test"
_SOURCE = "korg:contract"


def _client(journal_path: str) -> LocalJournalClient:
    return LocalJournalClient(journal_path=journal_path, source_agent=_SOURCE)


def _events(journal_path: str) -> list:
    if not os.path.exists(journal_path):
        return []
    with open(journal_path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def offer(journal_path: str, frm: str, to: str, task: str, criteria: str,
          *, deadline: str | None = None) -> int:
    """Requester offers a task with acceptance criteria. Returns the offer's seq_id."""
    return _client(journal_path).record_tool_call(
        OFFER, {"from": frm, "to": to, "task": task, "criteria": criteria, "deadline": deadline},
        {}, True, 0)


def accept(journal_path: str, frm: str, offer_seq: int) -> int:
    """Deliverer accepts the offer (causally links to it)."""
    return _client(journal_path).record_tool_call(
        ACCEPT, {"from": frm}, {}, True, 0, triggered_by=offer_seq)


def commit(journal_path: str, frm: str, deliverable, *, salt: str | None = None,
           sign_with: str | None = None) -> tuple[int, str]:
    """Seal the deliverable before the deadline. Records only the commit hash (the work
    stays hidden). With ``sign_with`` (the deliverer's Ed25519 private key) the seal is
    SIGNED — the event carries the deliverer's pubkey + a signature over the commit, so
    'who sealed this' is provable, not an unsigned name. Returns ``(seq_id, salt)``."""
    commit_hash, salt = SE.seal(deliverable, salt)
    args = {"from": frm, "commit": commit_hash}
    if sign_with is not None:
        args["pubkey"] = SG.public_of(sign_with)
        args["sig"] = SG.sign_tip(sign_with, commit_hash)   # sign the seal hash (32 bytes)
    seq = _client(journal_path).record_tool_call(COMMIT, args, {}, True, 0)
    return seq, salt


def mark_deadline(journal_path: str, frm: str) -> int:
    """Post the deadline marker. A commit AFTER this event (by seq) is a default."""
    return _client(journal_path).record_tool_call(DEADLINE, {"from": frm}, {}, True, 0)


def reveal(journal_path: str, frm: str, commit_seq: int, deliverable, salt: str) -> int:
    """Open the envelope. Anyone can now recompute the commit and confirm it matches."""
    return _client(journal_path).record_tool_call(
        REVEAL, {"from": frm, "deliverable": deliverable, "salt": salt},
        {}, True, 0, triggered_by=commit_seq)


def record_test(journal_path: str, frm: str, commit_seq: int, passed: bool, detail: str = "") -> int:
    """Record the acceptance-test outcome against the deliverable."""
    return _client(journal_path).record_tool_call(
        TEST, {"from": frm, "passed": bool(passed), "detail": detail},
        {}, True, 0, triggered_by=commit_seq)


def verdict(journal_path: str) -> dict:
    """Resolve the deal as a pure function of the chain. Never mutates anything."""
    events = _events(journal_path)
    pick = lambda t: [e for e in events if e.get("tool_name") == t]  # noqa: E731
    offers, commits = pick(OFFER), pick(COMMIT)
    deadlines, reveals, tests = pick(DEADLINE), pick(REVEAL), pick(TEST)

    if not offers:
        return {"status": "no-contract", "why": "no offer on the chain"}
    commit_ev = commits[0] if commits else None
    deadline_ev = deadlines[0] if deadlines else None

    if commit_ev is None:
        return {"status": "DEFAULTED", "why": "no deliverable was sealed", "signed_by": None}

    # who sealed it? a valid signature over the commit binds the seal to a key (not a name)
    ca = commit_ev["args"]
    signed_by = (ca["pubkey"] if ca.get("pubkey") and ca.get("sig")
                 and SG.verify_tip(ca["pubkey"], ca["commit"], ca["sig"]) else None)

    def out(status: str, why: str) -> dict:
        return {"status": status, "why": why, "signed_by": signed_by}

    if deadline_ev is not None and commit_ev["seq_id"] > deadline_ev["seq_id"]:
        return out("DEFAULTED", "sealed only after the deadline")
    if not reveals:
        return out("PENDING", "sealed but not yet revealed")
    rev = reveals[0]["args"]
    if not SE.verify(rev["deliverable"], rev["salt"], ca["commit"]):
        return out("FRAUD", "the revealed deliverable does not match what was sealed")
    if not tests:
        return out("REVEALED", "reveal matches the seal; awaiting acceptance test")
    if not tests[0]["args"]["passed"]:
        return out("FAILED", "deliverable does not pass the acceptance criteria")
    return out("SETTLED", "sealed before the deadline, reveal matches the seal, passed acceptance")
