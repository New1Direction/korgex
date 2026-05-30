"""The sealed-envelope (commit-reveal) primitive for korg.

Seal a value BEFORE an outcome; reveal it AFTER. The commit is a SHA-256 over the
korg-ledger@v1 canonical encoding of ``{"payload": value, "salt": salt}`` — so a
reveal recomputes byte-for-byte identically in Python, Rust, or the browser
(see ``sealCommit`` in assets/korg_verify.js). The salt hides the value and makes
the commit non-brute-forceable; ``verify`` proves a revealed value is exactly what
was sealed.

This is the shared foundation under every roadmap "receipt": a forecast sealed
before its deadline, a deliverable sealed before review, a record sealed at intake.
What it proves: the reveal is unchanged from the commit. What it does NOT prove on
its own: *when* the commit was made — that requires anchoring the chain's tip to an
external clock (see ledger_spec.verify_chain(expected_tip=...)).
"""
from __future__ import annotations

import hashlib
import secrets

from src import ledger_spec as S


def commit_for(value, salt: str) -> str:
    """The commitment hash for ``value`` under ``salt`` (lowercase hex SHA-256)."""
    return hashlib.sha256(S.canonicalize({"payload": value, "salt": salt})).hexdigest()


def seal(value, salt: str | None = None) -> tuple[str, str]:
    """Seal ``value``. Returns ``(commit, salt)``. A fresh 16-byte random salt is
    generated unless one is supplied, so the same value seals to a different commit
    each time and the value cannot be recovered from the commit."""
    if salt is None:
        salt = secrets.token_hex(16)
    return commit_for(value, salt), salt


def verify(value, salt: str, commit: str) -> bool:
    """True iff ``value`` under ``salt`` reproduces ``commit`` (constant-time compare)."""
    return secrets.compare_digest(commit_for(value, salt), commit)
