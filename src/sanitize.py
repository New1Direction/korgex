"""Redact secrets from data before it is persisted to the ledger.

korgex journals are tamper-evident AND shareable (`korgex audit --html`). A
credential that slips into a tool's args/result would be published alongside the
proof. `redact()` scrubs known secret *shapes* (provider keys, tokens, AWS keys,
JWTs, bearer headers, PEM private-key blocks) and any value under a secret-named
key, recursively — so a verifiable record is also safe to hand to anyone.

Applied only at the persistence boundary; the conversation the model sees is
untouched. Idempotent (re-redacting is a no-op) and structure/type preserving.
"""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Field names whose VALUE is a secret regardless of shape.
_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|passwd|password|pwd|authorization|"
    r"access[_-]?key|private[_-]?key|client[_-]?secret|credential|session[_-]?key)"
)

# Value shapes that are secrets wherever they appear inside a string.
_VALUE_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{12,}"),
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"pypi-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
]


def _redact_string(s: str) -> str:
    for pat in _VALUE_RES:
        s = pat.sub(REDACTED, s)
    return s


# Key names that CONTAIN "token" but are token COUNTS / config, not credentials —
# so they're never redacted (otherwise cost/audit data is needlessly destroyed).
_NOT_SECRET_KEYS = frozenset({
    "prompt_tokens", "completion_tokens", "total_tokens", "tokens",
    "input_tokens", "output_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "max_tokens", "max_output_tokens",
    "tokens_before", "tokens_after",  # cache-aware compaction event counts
    # the disjoint prompt-cache breakdown recorded on llm_inference events —
    # provable cache hits + honest cost; counts, never credentials.
    "cache_read_tokens", "cache_creation_tokens", "uncached_input_tokens",
})


def _is_secret_key(key) -> bool:
    if not isinstance(key, str):
        return False
    if key.lower() in _NOT_SECRET_KEYS:
        return False                       # token COUNTS aren't secrets
    return bool(_SECRET_KEY_RE.search(key))


def redact(value):
    """Return a copy of `value` with secrets replaced by ``[REDACTED]``."""
    if isinstance(value, dict):
        return {k: (REDACTED if _is_secret_key(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value
