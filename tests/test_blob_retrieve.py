"""Tests for the READ side of the ledger blob store: read_blob + blob_path_for.

The HARD INVARIANT is NEVER LOSE DATA: _write_blob seals exact bytes, read_blob
returns them byte-for-byte AND re-verifies the sha256. A missing blob or an
on-disk corruption is an integrity failure → abort loudly (ValueError). No
second store is introduced; this reads the same content-addressed tree
_write_blob writes.
"""
from __future__ import annotations

import hashlib

import pytest

from src import korg_ledger as kl


def test_read_blob_round_trip_sha_prefix_and_bare_hex(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    original = b"hello world" * 500
    sha, size = kl._write_blob(original)
    assert size == len(original)
    # "sha256:" prefix form
    assert kl.read_blob(f"sha256:{sha}") == original
    # bare hex form
    assert kl.read_blob(sha) == original


def test_blob_path_for_matches_write_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    data = b"x" * 4096
    sha, _ = kl._write_blob(data)
    p = kl.blob_path_for(sha)
    assert p.exists()
    assert p.read_bytes() == data
    # sharded by first two hex chars
    assert p.parent.name == sha[:2]


def test_read_blob_missing_raises_value_error(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    missing = "0" * 64
    with pytest.raises(ValueError) as ei:
        kl.read_blob(f"sha256:{missing}")
    msg = str(ei.value).lower()
    assert "not found" in msg or "missing" in msg


def test_read_blob_corruption_raises_integrity_error(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    original = b"the real bytes" * 100
    sha, _ = kl._write_blob(original)
    # Tamper with the on-disk blob (rewrite different bytes at its path).
    p = kl.blob_path_for(sha)
    p.write_bytes(b"TAMPERED" * 100)
    with pytest.raises(ValueError) as ei:
        kl.read_blob(sha)
    msg = str(ei.value).lower()
    assert "mismatch" in msg or "sha" in msg or "integrity" in msg


def test_read_blob_returns_exact_bytes_for_arbitrary_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("KORG_BLOB_DIR", str(tmp_path))
    blob = bytes(range(256)) * 64
    sha, _ = kl._write_blob(blob)
    got = kl.read_blob(sha)
    assert got == blob
    assert hashlib.sha256(got).hexdigest() == sha
