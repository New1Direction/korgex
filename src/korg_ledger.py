"""
korg_ledger.py — korgex ledger client (schema v1.0)

Posts AgentToolCall events to a running korg web server at POST /api/agent/tool-call.
Returns the assigned seq_id so callers can wire triggered_by on subsequent events.

Design rules (see agent_event_spec.md in the korg repo):

  §1  One event per completed tool call. Call record_tool_call() after the tool returns.
      If the agent crashes before a call completes, no event is written. Correct behavior.

  §2  triggered_by: seq_id of the event that caused this call (None for root events).
      Internal tool composition is not ledgered — only calls at the agent decision boundary.
      Parallel tool calls from the same LLM batch share triggered_by (they are siblings).
      Retry's triggered_by points at the failure event, not the original call.

  §2a llm_inference parent rule. Round-N's llm_inference.triggered_by points at
      round-(N-1)'s llm_inference seq_id — NOT at the most recent tool call from
      round-(N-1). The cause of round-N's inference is the prior inference's
      decision to keep going, not any specific tool result.

      Naive "chain to the most recent emitted event" implementations look right
      on inspection and pass topological checks, but break rewind semantics:
      replaying from a tool_call seq backward skips the llm_inference that
      actually produced the next round's prompt.

      WRONG (naive chaining):
        seq=1 user_prompt   triggered_by=None
        seq=2 llm_inference triggered_by=1
        seq=3 Edit          triggered_by=2
        seq=4 llm_inference triggered_by=3   ← wrong: round 2's LLM was not
                                                caused by the Edit, it was
                                                caused by round 1's LLM
                                                deciding to take another turn

      CORRECT:
        seq=4 llm_inference triggered_by=2   ← round-(N-1)'s llm_inference

      Tool calls within a round are still siblings under that round's
      llm_inference (rule §2): seq=3 Edit's triggered_by remains 2.

  §3  1 KB threshold applied uniformly. Hashing convention:
        - JSON field values: compact JSON → UTF-8 bytes → SHA-256
        - String values: UTF-8 bytes → SHA-256
        - Binary values: raw bytes → SHA-256
      Two agents emitting the same content MUST produce the same SHA-256.
      Blobs are written before events (blob-first atomicity).
      Missing blobs on replay are a ledger integrity failure — abort loudly.

  §4  Originator is determined by walking triggered_by back to the root user_prompt.
      There is no separate originator field. The causal chain is the audit answer.

  §5  Client serializes ledger writes (spec §7.5). The background thread issues exactly
      one HTTP request at a time. The agent loop enqueues non-blocking; if the queue
      fills (maxsize=256), the oldest item is dropped with a WARNING. Never blocks the
      agent loop. Never grows unboundedly.

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
import queue
import threading
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# korg_dogfood.py validates events carry this; missing == rejected (spec §1.0).
SCHEMA_VERSION = "1.0"

# Any field value serialising to more than this many bytes is content-addressed.
# Applied uniformly — no exceptions for "small" payloads. (spec §3)
CONTENT_REF_THRESHOLD_BYTES = 1024

# Background writer queue capacity (spec §7.5).
# If korg is unreachable and 256 events pile up, oldest is dropped with a warning.
_QUEUE_MAXSIZE = 256

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
# Hashing — spec §7.2
# ---------------------------------------------------------------------------

def _canonical_bytes(value: Any) -> bytes:
    """
    Return the canonical byte representation of a value for SHA-256 hashing.

    Convention (spec §7.2):
    - Structured (dict/list): compact JSON → UTF-8
    - str: UTF-8 directly
    - bytes: raw bytes
    - Everything else: compact JSON → UTF-8

    Two agents hashing the same logical content MUST produce the same digest.
    """
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    # Structured value: canonical JSON, no extra whitespace
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Blob storage — spec §3, §7.3
# ---------------------------------------------------------------------------

def _write_blob(data: bytes) -> tuple[str, int]:
    """Write a blob to the local blob store. Returns (sha256, size_bytes).

    Blob is written before the event is appended (blob-first atomicity).
    Missing blobs on replay are a ledger integrity failure (spec §7.3).
    """
    digest = _sha256(data)
    prefix = digest[:2]
    dest = _blob_dir() / prefix / digest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return digest, len(data)

    # Atomic write: tmp file → fsync → rename. A concurrent writer landing the
    # same blob first is fine — os.replace is atomic and the content is
    # content-addressed, so the result is identical either way.
    tmp = dest.with_suffix(dest.suffix + f".tmp.{os.getpid()}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return digest, len(data)


# ---------------------------------------------------------------------------
# Content-ref helpers — spec §3
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

    Uses canonical byte representation per spec §7.2 so two agents hashing the
    same content produce the same SHA-256.
    """
    data = _canonical_bytes(value)
    if len(data) <= CONTENT_REF_THRESHOLD_BYTES:
        return value

    sha256, size_bytes = _write_blob(data)
    payload_refs.append({"sha256": sha256, "size_bytes": size_bytes, "label": label})
    return {"_ref": f"sha256:{sha256}", "size_bytes": size_bytes}


# ---------------------------------------------------------------------------
# Background writer — spec §7.5
# ---------------------------------------------------------------------------

class _LedgerWriter(threading.Thread):
    """
    Daemon thread that drains the write queue one request at a time.

    Exactly one HTTP request is in-flight at any moment, preserving the order
    in which events were enqueued — and therefore the seq_id ordering that
    triggered_by depends on (spec §7.5).
    """

    def __init__(self, endpoint: str, timeout_secs: float) -> None:
        super().__init__(daemon=True, name="korg-ledger-writer")
        self._endpoint = endpoint
        self._timeout = timeout_secs
        self._q: queue.Queue[dict | None] = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._seq_results: dict[int, int] = {}  # enqueue_id → seq_id
        self._enqueue_counter = 0
        self._lock = threading.Lock()
        self.start()

    def enqueue(self, body: dict) -> None:
        """Put a request body on the queue. Non-blocking; drops oldest if full."""
        try:
            self._q.put_nowait(body)
        except queue.Full:
            # Queue full means korg has been unreachable for >256 events.
            # Drop oldest (get + discard) then enqueue new item.
            try:
                self._q.get_nowait()
                self._q.task_done()
            except queue.Empty:
                pass
            logger.warning(
                "[korg] write queue full (%d slots) — oldest event dropped. "
                "Ledger integrity is best-effort while korg is unreachable.",
                _QUEUE_MAXSIZE,
            )
            try:
                self._q.put_nowait(body)
            except queue.Full:
                pass  # extremely unlikely; give up on this event

    def stop(self) -> None:
        """Signal the writer thread to exit cleanly."""
        self._q.put(None)  # sentinel

    def run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                break
            try:
                resp = requests.post(
                    self._endpoint,
                    json=item,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                seq_id = resp.json().get("seq_id")
                logger.debug("[korg] recorded %s → seq=%s", item.get("tool_name"), seq_id)
            except Exception as exc:
                logger.warning("[korg] write failed (%s): %s", item.get("tool_name"), exc)
            finally:
                self._q.task_done()


# ---------------------------------------------------------------------------
# Ledger client
# ---------------------------------------------------------------------------

class KorgLedgerClient:
    """
    Serialized, non-blocking client for posting AgentToolCall events to korg.

    Serialized: exactly one HTTP request in-flight at a time via background
    thread. This preserves causal ordering of seq_ids (spec §7.5).

    Non-blocking: record_tool_call() returns immediately. The agent loop is
    never delayed by ledger writes or korg availability.

    Usage::

        client = KorgLedgerClient()

        # Root event — the user's prompt (triggered_by=None)
        root_seq = client.record_user_prompt("add a /healthz endpoint")

        # Then for each tool call, pass the triggering seq_id
        seq = client.record_tool_call(
            tool_name="Edit",
            args={"file_path": "src/routes.py", ...},
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
        self._writer: _LedgerWriter | None = None

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
    ) -> None:
        """
        Enqueue one AgentToolCall event. Returns immediately.

        The event is written by the background thread in enqueue order,
        preserving causal ordering. Failures are logged, never raised.

        Note: returns None (not a seq_id) because the write is async.
        To chain triggered_by, use record_user_prompt() and record_llm_call()
        which return seq_ids synchronously via a blocking probe call.
        See agent_event_spec.md §7.5 for the ordering rationale.
        """
        if not self._is_available():
            return

        payload_refs: list[dict] = []

        # Apply 1 KB content-ref threshold uniformly (spec §3 + §7.2)
        safe_args = _maybe_content_ref(args, f"{tool_name}.args", payload_refs)
        safe_result = _maybe_content_ref(result, f"{tool_name}.result", payload_refs)

        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
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

        self._get_writer().enqueue(body)

    def record_user_prompt(self, prompt: str) -> int | None:
        """
        Emit the root AgentToolCall for a user prompt synchronously.

        This is the only synchronous call because we need the seq_id to wire
        triggered_by on the first LLM event. Blocks for at most timeout_secs.
        Returns the assigned seq_id, or None if korg is unavailable.
        """
        return self._post_sync(
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
        assistant_text: str | None = None,
    ) -> int | None:
        """
        Emit an LLM inference event synchronously.

        Synchronous because all parallel tool calls from the same LLM response
        need triggered_by=<this seq_id>. Blocks for at most timeout_secs.
        Returns the assigned seq_id, or None if korg is unavailable.

        v0.3.2: pass `assistant_text` so the reply text lands on the event's
        `result` field — downstream consumers (KorgChat /recall, audit
        replay) can then grep the journal for what the model actually said,
        not just token counts. None preserves the v0.3.1 on-disk shape.
        """
        result: dict[str, Any] = {"completion_tokens": completion_tokens}
        if assistant_text is not None:
            result["text"] = assistant_text
        return self._post_sync(
            tool_name="llm_inference",
            args={"model": model, "prompt_tokens": prompt_tokens},
            result=result,
            success=True,
            duration_ms=duration_ms,
            triggered_by=triggered_by,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_sync(
        self,
        tool_name: str,
        args: Any,
        result: Any,
        success: bool,
        duration_ms: int,
        triggered_by: int | None,
    ) -> int | None:
        """Blocking post — used only for root/llm events that need the seq_id back."""
        if not self._is_available():
            return None

        payload_refs: list[dict] = []
        safe_args = _maybe_content_ref(args, f"{tool_name}.args", payload_refs)
        safe_result = _maybe_content_ref(result, f"{tool_name}.result", payload_refs)

        body: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
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
            resp = requests.post(self._endpoint, json=body, timeout=self.timeout_secs)
            resp.raise_for_status()
            seq_id: int = resp.json()["seq_id"]
            logger.debug("[korg] sync recorded %s → seq=%d", tool_name, seq_id)
            return seq_id
        except Exception as exc:
            logger.warning("[korg] sync write failed (%s): %s", tool_name, exc)
            return None

    def _get_writer(self) -> _LedgerWriter:
        if self._writer is None:
            self._writer = _LedgerWriter(self._endpoint, self.timeout_secs)
        return self._writer

    def _is_available(self) -> bool:
        if self._available is True:
            return True
        if self._available is False:
            return False

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
# In-process bridge client (v0.3.0). Same public API as KorgLedgerClient but
# writes via the PyO3 `korg_bridge` extension instead of HTTP. No background
# thread, no queue, no server dependency — the write happens inline and the
# returned seq_id is the journal's actual assignment.
# ---------------------------------------------------------------------------


class KorgBridgeClient:
    """In-process equivalent of KorgLedgerClient.

    Constructed when the `korg_bridge` Python extension is importable. Same
    three public methods as the HTTP client so call sites can swap freely.
    Each method is synchronous because the Rust side is microsecond-scale;
    the background queue the HTTP client needed exists to hide HTTP latency,
    which doesn't apply here.

    The on-disk journal format is identical to what korg-server writes via
    HTTP — a server can be launched against the same journal after the fact.
    """

    def __init__(
        self,
        journal_path: str | None = None,
        source_agent: str | None = None,
    ) -> None:
        import korg_bridge  # local import: only required when this path is used

        journal_path = journal_path or os.environ.get(
            "KORG_JOURNAL_PATH", str(Path(".korg") / "journal.json")
        )
        self.source_agent = source_agent or _agent_identity()
        self._bridge = korg_bridge.Bridge(journal_path)

    def record_tool_call(
        self,
        tool_name: str,
        args: Any,
        result: Any,
        success: bool,
        duration_ms: int,
        triggered_by: int | None = None,
    ) -> int:
        payload_refs: list[dict] = []
        safe_args = _maybe_content_ref(args, f"{tool_name}.args", payload_refs)
        safe_result = _maybe_content_ref(result, f"{tool_name}.result", payload_refs)
        # v0.3.1: payload_refs flow-through. _maybe_content_ref has already
        # written any large blob bytes to .korg/blobs/ and populated this
        # list with ContentRef dicts ({sha256, size_bytes, label}). The
        # bridge now records them on the event itself so the journal carries
        # a complete index — matching what the HTTP path has always done.
        return self._bridge.record_tool_call(
            source_agent=self.source_agent,
            tool_name=tool_name,
            args=safe_args,
            result=safe_result,
            success=success,
            duration_ms=int(duration_ms),
            triggered_by=triggered_by,
            payload_refs=payload_refs,
        )

    def record_user_prompt(self, prompt: str) -> int:
        return self._bridge.record_user_prompt(prompt)

    def record_llm_call(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: int,
        triggered_by: int | None,
        assistant_text: str | None = None,
    ) -> int:
        """v0.3.2: forward `assistant_text` to the bridge so the reply text
        lands on the event's `result.text`. Matches the HTTP client's new
        signature so callers stay uniform regardless of which transport
        get_default_client() chose."""
        return self._bridge.record_llm_call(
            model=model,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            duration_ms=int(duration_ms),
            triggered_by=triggered_by,
            source_agent=self.source_agent,
            assistant_text=assistant_text,
        )


# ---------------------------------------------------------------------------
# Module-level default client (lazily initialised)
# ---------------------------------------------------------------------------

_default_client: "KorgLedgerClient | KorgBridgeClient | None" = None


def get_default_client() -> "KorgLedgerClient | KorgBridgeClient":
    """Return the process-wide default ledger client (created on first call).

    Prefers the in-process Bridge when korg_bridge is importable; falls back
    to the HTTP client otherwise. Set KORGEX_LEDGER=http to force the HTTP
    path (useful when debugging the network protocol)."""
    global _default_client
    if _default_client is None:
        force = os.environ.get("KORGEX_LEDGER", "auto").lower()
        if force == "http":
            _default_client = KorgLedgerClient()
        elif force == "bridge":
            _default_client = KorgBridgeClient()
        else:
            try:
                _default_client = KorgBridgeClient()
                logger.info("[korg] using in-process bridge for ledger writes")
            except ImportError:
                _default_client = KorgLedgerClient()
                logger.info("[korg] korg_bridge not installed; falling back to HTTP ledger client")
    return _default_client

