"""PARENT<->KERNEL WIRE PROTOCOL — the single source of truth for message shapes.

Both the parent (``KernelHandle``) and the child (``kernel_main``) import the
constants and the ``encode``/``decode`` helpers from HERE so the shapes can never
drift between the two implementers.

== Transport / framing ==
- Channel: the kernel runs as ``Popen([sys.executable, "-u", "-m",
  "src.codeact.kernel_main"], stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True,
  bufsize=1)``. Parent writes requests to ``kernel.stdin``; kernel writes
  responses to ``kernel.stdout``. stderr is captured separately for crash
  diagnostics ONLY (NEVER parsed for protocol).
- Framing: ONE JSON object per line (newline-delimited JSON / NDJSON), UTF-8, no
  embedded newlines (``json.dumps`` default escapes them). Every message ends
  with ``"\n"`` and the writer flushes. The line channel carries framed JSON and
  NOTHING ELSE — all human/debug text goes to stderr. This is the #1 wire risk
  (stray prints corrupt the stream), so user-code stdout/stderr is redirected
  into capture buffers and never touches the real stdout pipe.
- Every message has a top-level string field ``"type"``. Unknown types are a
  protocol error → the parent resets the kernel.
"""

from __future__ import annotations

import json
from typing import Any

# ── Message type tags ────────────────────────────────────────────────────────
# PARENT -> KERNEL
TYPE_EXEC = "exec"
TYPE_TOOL_RESULT = "tool_result"
TYPE_PING = "ping"
# KERNEL -> PARENT
TYPE_READY = "ready"
TYPE_TOOL_CALL = "tool_call"
TYPE_EXEC_RESULT = "exec_result"
TYPE_PONG = "pong"


def encode(obj: dict) -> str:
    """Serialize one protocol message to a single NDJSON line (with trailing \\n).

    ``json.dumps`` default-escapes embedded newlines so the one-object-per-line
    framing invariant holds even when a payload string contains ``\\n``. We pass
    ``default=str`` as a final guard so a stray non-serializable value degrades to
    its ``repr`` rather than raising mid-write (which would desync the stream).
    """
    return json.dumps(obj, default=str) + "\n"


def decode(line: str) -> dict:
    """Parse one NDJSON line into a message dict.

    Raises ``json.JSONDecodeError`` on a malformed line (the parent treats that as
    a kernel crash; the child treats it as a fatal protocol error). A decoded
    value that is not a dict is coerced to a protocol error too.
    """
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ValueError(f"protocol line is not a JSON object: {type(obj).__name__}")
    return obj


# ── Builders (canonical message shapes) ───────────────────────────────────────
# Centralizing construction keeps field names identical on both ends.

def exec_request(exec_id: str, code: str, fuel: dict) -> dict:
    return {"type": TYPE_EXEC, "id": exec_id, "code": code, "fuel": fuel}


def tool_result_ok(call_id: str, result: Any) -> dict:
    return {"type": TYPE_TOOL_RESULT, "call_id": call_id, "ok": True, "result": result}


def tool_result_err(call_id: str, error: str) -> dict:
    return {"type": TYPE_TOOL_RESULT, "call_id": call_id, "ok": False, "error": error}


def ping(ping_id: str) -> dict:
    return {"type": TYPE_PING, "id": ping_id}


def ready(pid: int, py: str) -> dict:
    return {"type": TYPE_READY, "pid": pid, "py": py}


def tool_call(call_id: str, exec_id: str, name: str, args: dict) -> dict:
    return {
        "type": TYPE_TOOL_CALL,
        "call_id": call_id,
        "exec_id": exec_id,
        "name": name,
        "args": args,
    }


def exec_result_ok(exec_id: str, *, stdout: str, stderr: str, result: Any,
                   truncated: bool, fuel: dict, mem_limit: str = None) -> dict:
    msg = {
        "type": TYPE_EXEC_RESULT,
        "id": exec_id,
        "ok": True,
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
        "truncated": truncated,
        "fuel": fuel,
    }
    if mem_limit is not None:
        msg["mem_limit"] = mem_limit
    return msg


def exec_result_err(exec_id: str, *, error: str, traceback: str, stdout: str,
                    stderr: str, fuel: dict, mem_limit: str = None) -> dict:
    msg = {
        "type": TYPE_EXEC_RESULT,
        "id": exec_id,
        "ok": False,
        "error": error,
        "traceback": traceback,
        "stdout": stdout,
        "stderr": stderr,
        "result": None,
        "fuel": fuel,
    }
    if mem_limit is not None:
        msg["mem_limit"] = mem_limit
    return msg


def pong(ping_id: str) -> dict:
    return {"type": TYPE_PONG, "id": ping_id}
