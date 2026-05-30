"""witness — a korg-ledger@v1 tap for any tool-dispatch loop.

Wrap a single `handle_tool(name, arguments) -> result` choke point — an MCP server,
an agent loop, a CLI router — so every call becomes a chained, tamper-evident,
replayable event. Adopt with two lines, right after `handle_tool` is defined:

    from witness import tap
    handle_tool = tap(handle_tool)          # opt-in via $KORG_TAP_JOURNAL

Self-contained: standard library only, no dependency on korgex. The journal it
writes verifies under korg-ledger@v1 — `korgex verify <journal>` and
`korgex audit --html` work directly on it.

Two guarantees for wrapping a production dispatcher:
  * pass-through — the wrapped function returns the underlying result unchanged;
  * fail-safe   — a ledger error can NEVER break a tool call (logging is best-effort).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

SPEC_VERSION = "korg-ledger@v1"
GENESIS_HASH = "0" * 64
_INLINE_LIMIT = 2048  # results bigger than this are stored as a hash ref, not inline


def canonicalize(value) -> bytes:
    """korg-ledger@v1 canonical form: sorted keys, compact, non-ASCII \\uXXXX-escaped."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def chain_hash(event: dict, key: bytes | None = None) -> str:
    """SHA-256 (or HMAC-SHA256 with a key) over the event minus its own entry_hash."""
    pre = {k: v for k, v in event.items() if k != "entry_hash"}
    data = canonicalize(pre)
    if key:
        return hmac.new(key, data, hashlib.sha256).hexdigest()
    return hashlib.sha256(data).hexdigest()


class LedgerTap:
    """Append-only korg-ledger@v1 writer that resumes an existing journal's chain."""

    def __init__(self, journal_path: str, source_agent: str = "witness", hmac_key=None):
        self.path = journal_path
        self.source_agent = source_agent
        self.key = hmac_key.encode() if isinstance(hmac_key, str) else hmac_key
        self.seq, self.prev_hash = self._resume()

    def _resume(self):
        seq, prev = 0, GENESIS_HASH
        try:
            with open(self.path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    e = json.loads(line)
                    seq = e.get("seq_id", seq)
                    prev = e.get("entry_hash", prev)
        except FileNotFoundError:
            pass
        return seq, prev

    def record(self, tool_name, args, result, success=True, duration_ms=0,
               triggered_by=None) -> dict:
        self.seq += 1
        event = {
            "schema_version": "1.0",
            "seq_id": self.seq,
            "source_agent": self.source_agent,
            "tool_name": tool_name,
            "args": args,
            "result": result,
            "success": bool(success),
            "duration_ms": int(duration_ms),
        }
        if triggered_by is not None:
            event["triggered_by"] = triggered_by
        event["prev_hash"] = self.prev_hash
        event["entry_hash"] = chain_hash(event, self.key)
        self.prev_hash = event["entry_hash"]
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")
        return event


def _jsonable(value):
    """Coerce arbitrary tool args/results to something canonicalizable."""
    if isinstance(value, dict):
        return value
    try:
        json.dumps(value)
        return {"value": value}
    except (TypeError, ValueError):
        return {"repr": str(value)[:200]}


def _summarize(result):
    """Keep the journal lean: small results inline; large/binary → content-hash ref."""
    try:
        blob = json.dumps(result, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return {"repr": str(result)[:200]}
    if len(blob) <= _INLINE_LIMIT:
        return _jsonable(result)
    return {"sha256": hashlib.sha256(blob.encode()).hexdigest(),
            "size_bytes": len(blob), "truncated": True}


def tap(handle_tool, journal_path: str | None = None, source_agent: str = "witness"):
    """Wrap a ``handle_tool(name, arguments) -> result`` so each call is recorded.

    No-op (returns ``handle_tool`` unchanged) unless a journal path is supplied via
    the argument or ``$KORG_TAP_JOURNAL`` — so it is disabled by default and adds
    zero overhead. With ``$KORG_LEDGER_HMAC_KEY`` set, the chain is tamper-PROOF.
    Recording is wrapped so a ledger failure never propagates to the caller.
    """
    journal_path = journal_path or os.environ.get("KORG_TAP_JOURNAL")
    if not journal_path:
        return handle_tool
    ledger = LedgerTap(journal_path, source_agent=source_agent,
                       hmac_key=os.environ.get("KORG_LEDGER_HMAC_KEY"))

    def wrapped(name, arguments):
        t0 = time.monotonic()
        result = handle_tool(name, arguments)
        try:
            success = not (isinstance(result, dict) and "error" in result)
            ledger.record(name, _jsonable(arguments), _summarize(result),
                          success=success, duration_ms=int((time.monotonic() - t0) * 1000))
        except Exception:
            pass  # audit logging is best-effort — it must never break a tool call
        return result

    return wrapped
