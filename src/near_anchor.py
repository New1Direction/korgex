"""NEAR anchoring payloads for Korgex receipts.

This module deliberately does *not* talk to NEAR itself. It creates a small,
privacy-preserving JSON artifact that a wallet, near-cli, NEAR Intents flow, or a
custom contract can publish on-chain. Only hashes and counts leave the machine;
the actual prompt/code/log contents stay local or in an explicitly supplied
artifact URI.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from src import ledger_spec as S
from src import receipt as RC
from src.korg_ledger import _ledger_hmac_key, load_journal_raw

SCHEMA = "korgex-near-anchor@v1"
DEFAULT_METHOD = "anchor"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_json(value: Any) -> str:
    import hashlib

    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _korgex_version() -> str:
    try:
        return version("korgex")
    except PackageNotFoundError:
        return "0.0.0+dev"


def _git_metadata(repo: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Return coarse git provenance without reading source contents."""
    cwd = str(repo or os.getcwd())

    def run(*args: str) -> str | None:
        try:
            r = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=2
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None

    head = run("rev-parse", "HEAD")
    root = run("rev-parse", "--show-toplevel")
    status = run("status", "--porcelain")
    return {
        "git_commit": head,
        "git_root_hash": _sha256_json(root) if root else None,
        "worktree_dirty": bool(status),
    }


def _tip(events: list[dict[str, Any]]) -> str:
    return events[-1].get("entry_hash") if events else S.GENESIS_HASH


def _verify_events(events: list[dict[str, Any]], *, key: bytes | None = None) -> None:
    errors = S.verify_dag(events) + S.verify_chain(events, key=key)
    if errors:
        raise ValueError("ledger does not verify: " + "; ".join(errors[:6]))


def _normalize_memo(memo: str | None) -> str | None:
    if memo is None:
        return None
    memo = memo.strip()
    return memo or None


def build_anchor(
    *,
    events: list[dict[str, Any]],
    receipt: dict[str, Any] | None = None,
    account_id: str | None = None,
    contract_id: str | None = None,
    network: str = "testnet",
    method_name: str = DEFAULT_METHOD,
    artifact_uri: str | None = None,
    memo: str | None = None,
    repo: str | os.PathLike[str] | None = None,
    generated_at: str | None = None,
    key: bytes | None = None,
) -> dict[str, Any]:
    """Build a NEAR-ready anchor artifact from verified ledger events.

    ``receipt`` is optional. When present it is hashed and linked, but the returned
    anchor still exposes only hashes. ``artifact_uri`` can point to an encrypted
    receipt bundle, GitHub Pages proof page, R2/IPFS object, etc.
    """
    events = list(events or [])
    _verify_events(events, key=key)

    if receipt is not None:
        verdict = RC.verify_receipt(receipt, key=key)
        if not verdict["ok"]:
            raise ValueError("receipt does not verify: " + "; ".join(verdict["errors"][:6]))
        if receipt.get("tip") != _tip(events):
            raise ValueError("receipt tip does not match journal tip")

    summary = RC.summarize(events)
    ledger_root = _tip(events)
    proof = {
        "spec": S.SPEC_VERSION,
        "ledger_root": ledger_root,
        "event_count": len(events),
        "journal_sha256": _sha256_json(events),
        "receipt_sha256": _sha256_json(receipt) if receipt is not None else None,
        "summary_sha256": _sha256_json(summary),
        "signed_by": ((receipt or {}).get("signature") or {}).get("pubkey"),
    }
    anchor = {
        "schema": SCHEMA,
        "generated_at": generated_at or _now_iso(),
        "target": {
            "chain": "NEAR",
            "network": network,
            "account_id": account_id,
            "contract_id": contract_id,
            "method_name": method_name,
        },
        "proof": proof,
        "artifact_uri": artifact_uri,
        "memo": _normalize_memo(memo),
        "korgex": {"version": _korgex_version()},
        "repo": _git_metadata(repo),
        "privacy": {
            "on_chain_contents": [
                "ledger_root",
                "event_count",
                "journal_sha256",
                "receipt_sha256",
                "artifact_uri",
                "memo",
            ],
            "excluded": ["prompts", "tool arguments", "tool results", "code", "secrets"],
        },
        "summary_preview": summary,
    }
    anchor["contract_call"] = {
        "method_name": method_name,
        "args": contract_args(anchor),
    }
    anchor["anchor_id"] = _sha256_json(anchor["contract_call"])[0:16]
    return anchor


def load_receipt_or_journal(
    path: str | os.PathLike[str], *, key: bytes | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    """Load either a korgex receipt JSON or a raw journal path."""
    p = Path(path)
    raw = p.read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict) and data.get("schema") == RC.SCHEMA:
        verdict = RC.verify_receipt(data, key=key)
        if not verdict["ok"]:
            raise ValueError("receipt does not verify: " + "; ".join(verdict["errors"][:6]))
        events = list(data.get("events") or [])
        return events, data, "receipt"

    events = load_journal_raw(str(p))
    _verify_events(events, key=key)
    return events, None, "journal"


def build_anchor_from_path(
    path: str | os.PathLike[str],
    *,
    account_id: str | None = None,
    contract_id: str | None = None,
    network: str = "testnet",
    method_name: str = DEFAULT_METHOD,
    artifact_uri: str | None = None,
    memo: str | None = None,
    repo: str | os.PathLike[str] | None = None,
    key: bytes | None = None,
) -> dict[str, Any]:
    key = _ledger_hmac_key() if key is None else key
    events, receipt, source_kind = load_receipt_or_journal(path, key=key)
    anchor = build_anchor(
        events=events,
        receipt=receipt,
        account_id=account_id,
        contract_id=contract_id,
        network=network,
        method_name=method_name,
        artifact_uri=artifact_uri,
        memo=memo,
        repo=repo,
        key=key,
    )
    anchor["source"] = {"kind": source_kind, "path": str(path)}
    return anchor


def contract_args(anchor: dict[str, Any]) -> dict[str, Any]:
    """The minimal args a NEAR contract needs to persist the anchor."""
    proof = anchor["proof"]
    return {
        "ledger_root": proof["ledger_root"],
        "event_count": proof["event_count"],
        "journal_sha256": proof["journal_sha256"],
        "receipt_sha256": proof.get("receipt_sha256"),
        "artifact_uri": anchor.get("artifact_uri"),
        "memo": anchor.get("memo"),
        "korgex_version": (anchor.get("korgex") or {}).get("version"),
    }


def near_cli_js_command(anchor: dict[str, Any], *, near_bin: str = "near") -> str:
    """Return a near-cli-js style transaction command for the generated payload."""
    target = anchor.get("target") or {}
    contract_id = target.get("contract_id") or "<contract.testnet>"
    account_id = target.get("account_id") or "<you.testnet>"
    network = target.get("network") or "testnet"
    method = target.get("method_name") or DEFAULT_METHOD
    args = json.dumps(contract_args(anchor), sort_keys=True, separators=(",", ":"))
    return (
        f"{near_bin} call {contract_id} {method} '{args}' "
        f"--accountId {account_id} --networkId {network}"
    )
