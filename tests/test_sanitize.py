"""Redact secrets before they reach the ledger.

korgex records every run to a tamper-evident chain — and those journals are now
SHAREABLE proofs (`korgex audit --html`). A credential that slipped into a tool's
args/result would be published with the proof. This scrubs known secret shapes and
secret-named fields at the ledger-write boundary, so a verifiable record is also
safe to share. The conversation the model sees is untouched — only what's persisted.
"""
from __future__ import annotations

from src.sanitize import redact

R = "[REDACTED]"


def test_redacts_values_under_secret_named_keys():
    out = redact({"api_key": "abc", "Token": "xyz", "password": "p", "normal": "keep", "count": 3})
    assert out["api_key"] == R and out["Token"] == R and out["password"] == R
    assert out["normal"] == "keep" and out["count"] == 3


def test_token_count_fields_are_not_redacted():
    # prompt_tokens/completion_tokens CONTAIN "token" but are counts, not secrets —
    # redacting them needlessly destroyed cost + audit data.
    out = redact({"prompt_tokens": 1234, "completion_tokens": 56, "max_tokens": 4096,
                  "tokens_before": 9000, "tokens_after": 1200,  # compaction event counts
                  "api_token": "sk-secret"})
    assert out["prompt_tokens"] == 1234        # kept
    assert out["completion_tokens"] == 56      # kept
    assert out["max_tokens"] == 4096           # kept
    # REGRESSION (found dogfooding on the wire): the cache-aware compaction event
    # records tokens_before/after — they CONTAIN "token" but are counts, and were
    # being nuked to [REDACTED] in the live ledger, destroying trace/cost data.
    assert out["tokens_before"] == 9000        # kept
    assert out["tokens_after"] == 1200         # kept
    assert out["api_token"] == R               # a real credential is STILL redacted


def test_redacts_known_secret_value_shapes_inside_strings():
    secrets = [
        "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX",
        "sk-or-v1-0123456789abcdef0123456789abcdef",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "github_pat_11ABCDEFG_abcdefghijklmnopqrstuvwxyz0123",
        "pypi-AgEIcHlwaS5vcmcABCDEFGHIJKLMNOPqrst",
        "AKIAIOSFODNN7EXAMPLE",
    ]
    for s in secrets:
        assert redact(f"the key is {s} ok") == "the key is [REDACTED] ok", s


def test_redacts_bearer_tokens_and_private_key_blocks():
    assert R in redact("Authorization: Bearer abcdef0123456789ABCDEFxyz")
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIveryprivate\n-----END RSA PRIVATE KEY-----"
    out = redact(pem)
    assert R in out and "MIIveryprivate" not in out


def test_recurses_into_nested_dicts_and_lists():
    out = redact({"a": [{"secret": "s"}, {"note": "sk-proj-ABCDEFGHIJKLMNOPQR0123456789"}]})
    assert out["a"][0]["secret"] == R
    assert R in out["a"][1]["note"]


def test_preserves_non_secret_values_and_types():
    payload = {"count": 3, "name": "hello world", "flag": True, "items": [1, 2, 3], "f": 1.5}
    assert redact(payload) == payload


def test_is_idempotent():
    once = redact({"api_key": "sk-proj-ABCDEFGHIJKLMNOPQR0123456789", "msg": "hi"})
    assert redact(once) == once
