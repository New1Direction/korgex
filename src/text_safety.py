"""Write-path text safety.

A production coding agent must never write control-byte garbage into a user's
source file. Model output occasionally carries stray C0 control characters — a
mangled em-dash once arrived as ``\\x1a\\x14`` — from a tokenization quirk, a
UTF-8 sequence split across stream chunks, or a bad decode somewhere upstream.
Rather than chase every possible source, we sanitize at the boundary: strip C0/DEL
control chars (keeping the legitimate whitespace tab/newline/CR) from any text
korgex is about to write. Printable text and all Unicode (em-dash, accents, CJK)
pass through untouched — only true control characters are removed.
"""
from __future__ import annotations

_ALLOWED_CONTROL = {"\t", "\n", "\r"}


def _keep(ch: str) -> bool:
    if ch in _ALLOWED_CONTROL:
        return True
    o = ord(ch)
    return o >= 0x20 and o != 0x7F        # printable + all Unicode; drop C0 and DEL


def strip_control_chars(text: str):
    """Remove C0/DEL control characters (except tab/newline/CR) from `text`.
    Returns ``(cleaned, removed_count)``. ``None``/empty pass through as-is."""
    if not text:
        return text, 0
    kept = [ch for ch in text if _keep(ch)]
    removed = len(text) - len(kept)
    if removed == 0:
        return text, 0
    return "".join(kept), removed
