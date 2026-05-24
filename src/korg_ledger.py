"""
korg_ledger.py — korgex ledger client (schema v1.0)

Posts AgentToolCall events to a running korg web server at POST /api/agent/tool-call.
Returns the assigned seq_id so callers can wire triggered_by on subsequent events.

Design rules (see agent_event_spec.md in the korg repo):
  - One event per completed tool call. Call record_tool_call() after the tool returns.
  - triggered_by: seq_id of the event that caused this call (None for root events).
  - Payloads over CONTENT_REF_THRESHOLD_BYTES are content-addressed automatically.
  - Blobs are written to BLOB_DIR keyed by SHA-256 before the event is posted.
  - Failures are logged, never raised. The agent loop must never halt for ledger reasons.

Actor identity convention:
  agent:<name>@<version>   — agent runtimes (e.g. "agent:korgex@0.2.2")
  human:<identifier>       — human overrides (e.g. "human:dusk")
  korg:<component>         — internal korg events (written by korg itself)
  mcp:<server-name>        — MCP server clients
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Any field value serialising to more than this many bytes is content-addressed.
# Applied uniformly — no exceptions for "small" payloads. (spec §3)
CONTENT_REF_THRESHOLD_BYTES = 1024

# Blob store location (v1). Must match the path korg expects.
# Override via KORG_BLOB_DIR env var.
_DEFAULT_BLOB_DIR = Path(".korg") / "blobs"


def _blob_dir() -> Path:
    return Path(os.environ.get("KORG_BLOB_DIR", str(_DEFAULT_BLOB_DIR)))


def _korg_url() -> str:
    return os.environ.get("KORG_URL", "http://localhost:8080")


def _agent_identity() -> str:
    """Return the canonical actor identity string for this korgex runtime."""
    try:
        from importlib.metadata import version
        ver = version("korgex")
    except Exception:
        ver = "dev"
    return f"agent:korgex@{ver}"


# ---------------------------------------------------------------------------
# Blob storage
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_blob(data: bytes) -> tuple[str, int]:
    """Write a blob to the local blob store. Returns (sha256, size_bytes)."""
    digest = _sha256(data)
    prefix = digest[:2]
    dest = _blob_dir() / prefix / digest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    return digest, len(data)


# ---------------------------------------------------------------------------
# Content-ref helpers
# ---------------------------------------------------------------------------

def _maybe_content_ref(
    value: Any,
    label: str,
    payload_refs: list[dict],
) -> Any:
    """
    If `value` serialises to more than CONTENT_REF_THRESHOLD_BYTES, write it
    to the blob store and return a content-ref sentinel dict. Otherwise return
    the value unchanged.
    """
    encoded = json.dumps(value, separators=(",", ":")).encode()
    if len(encoded) <= CONTENT_REF_THRESHOLD_BYTES:
        return value

    sha256, size_bytes = _write_blob(encoded)
    payload_refs.append({"sha256": sha256, "size_bytes": size_bytes, "label": label})
    return {"_ref": f"sha256:{sha256}", "size_bytes": size_bytes}


# ---------------------------------------------------------------------------
# Ledger client
# ---------------------------------------------------------------------------

class KorgLedgerClient:
    """
    Non-blocking client for posting AgentToolCall events to a korg web server.

    Usage::

        client = KorgLedgerClient()

        # At the start of a session, record the user's prompt as root event:
        root_seq = client.record_tool_call(
            tool_name="user_prompt",
            args={"prompt": user_input},
            result={},
            success=True,
            duration_ms=0,
            triggered_by=None,
        )

        # Then for each tool call, pass the triggering seq_id:
        seq = client.record_tool_call(
            tool_name="Edit",
            args={"file_path": "src/auth.py", ...},
            result={"success": True},
            success=True,
            duration_ms=142,
            triggered_by=root_seq,
        )
    """

    def __init__(
        self,
        base_url: str | None = None,
        source_agent: str | None = None,
        timeout_secs: float = 2.0,
    ) -> None:
        self.base_url = (base_url or _korg_url()).rstrip("/")
        self.source_agent = source_agent or _agent_identity()
        self.timeout_secs = timeout_secs
        self._endpoint = f"{self.base_url}/api/agent/tool-call"
        self._available: bool | None = None  # None = not yet probed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_tool_call(
        self,
        tool_name: str,
        args: Any,
        result: Any,
        success: bool,
        duration_ms: int,
        triggered_by: int | None = None,
    ) -> int | None:
        """
        Emit one AgentToolCall event. Returns the assigned seq_id (for use as
        triggered_by on subsequent events), or None if the ledger is unavailable.

        This method never raises. Failures are logged at WARNING level.
        """
        if not self._is_available():
            return None

        payload_refs: list[dict] = []

        # Apply 1 KB content-ref threshold uniformly to args and result
        safe_args = _maybe_content_ref(args, f"{tool_name}.args", payload_refs)
        safe_result = _maybe_content_ref(result, f"{tool_name}.result", payload_refs)

        body = {
            "source_agent": self.source_agent,
            "tool_name": tool_name,
            "args": safe_args,
            "result": safe_result,
            "payload_refs": payload_refs,
            "success": success,
            "duration_ms": duration_ms,
        }
        if triggered_by is not None:
            body["triggered_by"] = triggered_by

        try:
            resp = requests.post(
                self._endpoint,
                json=body,
                timeout=self.timeout_secs,
            )
            resp.raise_for_status()
            seq_id: int = resp.json()["seq_id"]
            logger.debug(
                "[korg] recorded %s → seq=%d (triggered_by=%s)",
                tool_name,
                seq_id,
                triggered_by,
            )
            return seq_id
        except Exception as exc:
            logger.warning("[korg] failed to record %s: %s", tool_name, exc)
            return None

    def record_user_prompt(self, prompt: str) -> int | None:
        """
        Convenience wrapper: emit the root AgentToolCall for a user prompt.
        Returns the seq_id to use as triggered_by for the first LLM call.
        """
        return self.record_tool_call(
            tool_name="user_prompt",
            args={"prompt": prompt},
            result={},
            success=True,
            duration_ms=0,
            triggered_by=None,
        )

    def record_llm_call(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: int,
        triggered_by: int | None,
    ) -> int | None:
        """
        Emit an event for an LLM inference call.
        The returned seq_id should be used as triggered_by for all tool calls
        spawned by this inference response (siblings in the causal tree).
        """
        return self.record_tool_call(
            tool_name="llm_inference",
            args={"model": model, "prompt_tokens": prompt_tokens},
            result={"completion_tokens": completion_tokens},
            success=True,
            duration_ms=duration_ms,
            triggered_by=triggered_by,
        )

    # ------------------------------------------------------------------
    # Availability probe
    # ------------------------------------------------------------------

    def _is_available(self) -> bool:
        """
        Probe whether the korg server is reachable. Result is cached after
        first successful probe; unreachable server logs once and stays quiet.
        """
        if self._available is True:
            return True
        if self._available is False:
            return False  # already failed, stay quiet

        try:
            r = requests.get(
                f"{self.base_url}/api/metrics",
                timeout=self.timeout_secs,
            )
            self._available = r.status_code == 200
        except Exception:
            self._available = False
            logger.info(
                "[korg] ledger unavailable at %s — tool calls will not be recorded. "
                "Start korg with --web to enable ledger integration.",
                self.base_url,
            )

        return bool(self._available)


# ---------------------------------------------------------------------------
# Module-level default client (lazily initialised)
# ---------------------------------------------------------------------------

_default_client: KorgLedgerClient | None = None


def get_default_client() -> KorgLedgerClient:
    """Return the process-wide default KorgLedgerClient (created on first call)."""
    global _default_client
    if _default_client is None:
        _default_client = KorgLedgerClient()
    return _default_client
