"""Proof-of-Custody — seal any file, prove it's unaltered. korg's first non-agent receipt.

Seal a file's fingerprint (sha256 of its bytes) onto the chain; optionally sign it
(the custodian's key) and anchor the tip to an external clock. Later, anyone re-hashes
the file in their own browser and confirms it is byte-identical to what was sealed.

The file's BYTES are never required — only its hash — so the file can stay private on
the holder's machine while its integrity is still publicly provable.

What this proves: the file is unaltered since the sealed (and anchored) moment, by a
known key. What it does NOT prove: that the content is TRUE, or that a camera captured
reality. Capture-truth is hardware attestation / C2PA's domain — korg is complementary,
sealing bytes + time + author, never reality.
"""
from __future__ import annotations

import json
import os

from src import signing as SG
from src.korg_ledger import LocalJournalClient

SEAL = "custody.seal"
_SOURCE = "korg:custody"


def _client(journal_path: str) -> LocalJournalClient:
    return LocalJournalClient(journal_path=journal_path, source_agent=_SOURCE)


def _events(journal_path: str) -> list:
    if not os.path.exists(journal_path):
        return []
    with open(journal_path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def seal_file(journal_path: str, file_hash: str, *, label: str | None = None,
              sign_with: str | None = None) -> int:
    """Seal a file by its sha256 ``file_hash``. With ``sign_with`` the custodian signs the
    hash, so 'who sealed it' is provable. Returns the seal event's seq_id."""
    args = {"hash": file_hash, "label": label}
    if sign_with is not None:
        args["pubkey"] = SG.public_of(sign_with)
        args["sig"] = SG.sign_tip(sign_with, file_hash)
    return _client(journal_path).record_tool_call(SEAL, args, {}, True, 0)


def verify_file(journal_path: str, file_hash: str) -> dict:
    """Is this exact file (by hash) sealed on the chain? Returns ``{sealed, label,
    signed_by, seq}`` — ``signed_by`` is the custodian's key iff its signature verifies."""
    seals = [e for e in _events(journal_path)
             if e.get("tool_name") == SEAL and (e.get("args") or {}).get("hash") == file_hash]
    if not seals:
        return {"sealed": False, "label": None, "signed_by": None, "seq": None}
    a = seals[0]["args"]
    signed_by = (a["pubkey"] if a.get("pubkey") and a.get("sig")
                 and SG.verify_tip(a["pubkey"], file_hash, a["sig"]) else None)
    return {"sealed": True, "label": a.get("label"), "signed_by": signed_by, "seq": seals[0]["seq_id"]}
