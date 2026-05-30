"""
ledger_spec.py — the korg-ledger@v1 reference implementation.

This is the FROZEN definition of korg's tamper-evident cognition ledger, lifted
out of korgex so it is owned by the spec, not by one app. It is deliberately
dependency-free (stdlib only: json, hashlib, hmac) so it can be vendored
anywhere and so the canonicalization is unambiguous enough to re-implement in
Rust/JS against the same conformance vectors (see spec/korg-ledger-v1/).

The guarantee: a sequence of events is hash-chained — each event carries
`prev_hash` (the previous event's `entry_hash`, or GENESIS for the first) and
`entry_hash` (the hash of its own canonical preimage). Any edit, deletion,
insertion, or reorder breaks the chain and is localized to a seq_id. With an
HMAC key the chain is tamper-PROOF (unforgeable without the key), not merely
tamper-evident.

The written specification lives at spec/korg-ledger-v1/SPEC.md; this module and
that document MUST agree, and both are pinned by spec/korg-ledger-v1/conformance.json.
"""

from __future__ import annotations

import hashlib
import hmac
import json

SPEC_VERSION = "korg-ledger@v1"

# The chain anchor: prev_hash of the first event in a journal.
GENESIS_HASH = "0" * 64

# Fields that ARE the hash/signature and so are excluded from the preimage.
_HASH_FIELDS = ("entry_hash",)


def canonicalize(value) -> bytes:
    """Canonical byte encoding of a value for hashing (korg-ledger@v1).

    Rules (must hold cross-language):
      - JSON serialization with object keys sorted lexicographically by code point;
      - no insignificant whitespace (item separator ",", key separator ":");
      - non-ASCII escaped as \\uXXXX (ASCII-only output) → no UTF-8 ambiguity.
    Two implementations hashing the same logical event MUST produce identical bytes.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def chain_hash(event: dict, key: bytes | None = None) -> str:
    """Compute an event's `entry_hash`.

    Preimage = canonical encoding of the event with its hash field(s) removed.
    `prev_hash` IS part of the preimage — that is what links each entry to the
    one before it. With `key`, the digest is HMAC-SHA256; otherwise SHA-256.
    Returns lowercase hex.
    """
    preimage = {k: v for k, v in event.items() if k not in _HASH_FIELDS}
    data = canonicalize(preimage)
    if key is not None:
        return hmac.new(key, data, hashlib.sha256).hexdigest()
    return hashlib.sha256(data).hexdigest()


def verify_chain(events: list, key: bytes | None = None,
                 expected_tip: str | None = None) -> list:
    """Recompute the hash-chain and report tampering. Returns [] iff intact.

    Each error is localized to a seq_id:
      - content edit         → `entry_hash` ≠ recomputed hash;
      - delete/insert/reorder → an event's `prev_hash` ≠ the prior event's
        `entry_hash` (broken link).
    With `key`, recomputation uses HMAC, so a tail rewritten without the key
    fails even though it is internally self-consistent.

    With `expected_tip` (a genuine tip hash anchored externally — a public
    timestamp, a signed post, a git tag), the chain's actual tip is compared to
    it. This is what closes the unkeyed-regeneration hole: a forger can edit a
    body and re-link + re-hash the whole downstream chain so the per-event checks
    above all pass, but the resulting tip cannot match a tip published before the
    forgery. Without an anchor (or a key), a fully regenerated chain is undetectable.
    """
    errors = []
    expected_prev = GENESIS_HASH
    for e in events:
        sid = e.get("seq_id")
        stored = e.get("entry_hash")
        if stored is None:
            errors.append(f"seq {sid}: missing entry_hash (event is not chained)")
            expected_prev = None
            continue
        if e.get("prev_hash") != expected_prev:
            errors.append(
                f"seq {sid}: prev_hash breaks the chain "
                f"(an event was inserted, deleted, or reordered)")
        if chain_hash(e, key=key) != stored:
            errors.append(f"seq {sid}: entry_hash mismatch (content was tampered)")
        expected_prev = stored
    if expected_tip is not None:
        actual_tip = events[-1].get("entry_hash") if events else None
        if actual_tip != expected_tip:
            errors.append(
                "tip does not match the anchored tip "
                "(chain may have been wholly regenerated/forged)")
    return errors


def verify_dag(events: list) -> list:
    """Check a list of ledger events forms a well-formed causal DAG.

    Returns a list of error strings ([] == valid). Invariants:
      - seq_ids are unique;
      - every `triggered_by` points to an existing seq_id that is STRICTLY EARLIER.
    The strictly-earlier rule is what makes rewind-by-truncation sound: cutting
    at seq N can never orphan a survivor, because a survivor's parent (< its own
    seq ≤ N) also survives.
    """
    errors = []
    seqs = [e.get("seq_id") for e in events]
    if len(seqs) != len(set(seqs)):
        errors.append("duplicate seq_id present")
    seqset = set(seqs)
    for e in events:
        tb = e.get("triggered_by")
        if tb is None:
            continue
        sid = e.get("seq_id")
        if tb not in seqset:
            errors.append(f"seq {sid}: triggered_by {tb} does not exist")
        elif sid is not None and tb >= sid:
            errors.append(f"seq {sid}: triggered_by {tb} is not strictly earlier")
    return errors


def rewind_events(events: list, target_seq: int) -> list:
    """Truncate events to seq_id <= target_seq. Pure; preserves order.

    Sound for branched DAGs precisely because edges point strictly backward
    (see verify_dag) — no survivor is ever left dangling.
    """
    return [e for e in events if (e.get("seq_id") is None or e["seq_id"] <= target_seq)]
