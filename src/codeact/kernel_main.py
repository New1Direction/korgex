"""PERSISTENT FUEL-METERED KERNEL — the subprocess entrypoint.

Runs INSIDE the child process spawned by ``KernelHandle``. Maintains a single
persistent namespace (``GLOBALS``) so variables/imports/defs survive across exec
requests — "code as an action space WITH memory". Captures stdout/stderr/the
last-expression value/exceptions, enforces an output-size cap and (on POSIX) an
``RLIMIT_AS`` memory cap, and speaks the NDJSON protocol on stdin/stdout
(diagnostics → stderr only). Wall-time fuel is enforced by the PARENT
(kill-on-deadline), NOT an in-kernel SIGALRM (an alarm would misfire while the
kernel legitimately blocks on a bridged tool round-trip).

The core logic (``execute_code``, ``run_request_loop``, ``build_globals``) is kept
importable and unit-testable WITHOUT the agent or a real subprocess: a test can
build globals with stub send/recv and call ``execute_code`` directly.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
import time
import traceback
from typing import Any, Callable

from . import protocol as P
from .bridge import make_stubs

try:  # POSIX-only; absent on Windows → memory cap degrades to a no-op
    import resource
except ImportError:  # pragma: no cover - exercised only on non-POSIX
    resource = None


# ── Output-size fuel: a StringIO that stops growing past a byte cap ───────────
class _CappedStringIO(io.StringIO):
    """A StringIO that bounds total bytes written and flips ``.truncated``.

    This is the OUTPUT-SIZE CAP fuel: it caps memory from runaway prints (the
    most common runaway) by silently dropping writes once the UTF-8 byte budget
    is hit. ``getvalue`` returns whatever fit. Capacity is measured in *bytes*
    (the wire is byte-budgeted) while the buffer holds text, so we track an
    independent running byte count.
    """

    def __init__(self, max_bytes: int):
        super().__init__()
        self._max = max(0, int(max_bytes))
        self._bytes = 0
        self.truncated = False

    def write(self, s):  # type: ignore[override]
        if not s:
            return 0
        if self._bytes >= self._max:
            self.truncated = True
            return 0
        chunk = s.encode("utf-8", "replace")
        remaining = self._max - self._bytes
        if len(chunk) <= remaining:
            self._bytes += len(chunk)
            return super().write(s)
        # Partial fit: take a prefix that fits the byte budget, then stop.
        clipped = chunk[:remaining].decode("utf-8", "ignore")
        self._bytes = self._max
        self.truncated = True
        super().write(clipped)
        return len(s)


def _apply_mem_limit(mem_mb: int) -> str:
    """Apply RLIMIT_AS once at boot on POSIX; return a status string.

    Returns ``"ok"`` when the cap was set, ``"unsupported"`` on non-POSIX or when
    the resource module / RLIMIT_AS is unavailable, and ``"error"`` if the syscall
    refused (e.g. a hard limit below the request). Per-exec re-tightening of
    RLIMIT_AS is not portable, so the cap is set ONCE from the first exec's
    mem_mb (or KORGEX_CODEACT_MEM_MB).
    """
    if os.name != "posix" or resource is None or not hasattr(resource, "RLIMIT_AS"):
        return "unsupported"
    try:
        mem_bytes = int(mem_mb) * 1024 * 1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        # Never raise above the inherited hard cap; clamp to it.
        new_hard = hard if hard != resource.RLIM_INFINITY and hard < mem_bytes else mem_bytes
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, new_hard))
        return "ok"
    except (ValueError, OSError):
        return "error"


def build_globals(send_fn: Callable[[dict], None],
                  recv_fn: Callable[[str], tuple],
                  exec_id_getter: Callable[[], str]) -> dict:
    """Create the persistent namespace with bridge stubs injected.

    A single dict serves as BOTH globals and locals for ``exec`` so top-level
    assignments persist across exec requests (the memory guarantee).
    """
    g: dict = {"__name__": "__codeact__", "__builtins__": __builtins__}
    g.update(make_stubs(send_fn, recv_fn, exec_id_getter))
    return g


def _split_last_expr(code: str):
    """Parse ``code``; if the final statement is a bare expression, return
    ``(exec_module, eval_expr)`` so the parent can exec the body then EVAL the
    last expression and capture its value. Otherwise ``(module, None)``.

    A SyntaxError here propagates to the caller, which reports it as an ok:false
    exec_result (recoverable — the kernel stays alive).
    """
    tree = ast.parse(code, mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body.pop()
        expr = ast.Expression(last.value)
        ast.copy_location(expr, last)
        return tree, expr
    return tree, None


def _serialize_result(value: Any, max_output: int) -> Any:
    """Make the last-expression value wire-safe.

    Prefer a JSON round-trip (so structured results stay structured for the
    model); fall back to a capped ``repr`` for arbitrary objects. NEVER let a
    non-serializable value break the exec_result emission.
    """
    if value is None:
        return None
    cap = max(0, int(max_output))
    try:
        serialized = json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)[:cap]
    # A huge JSON-serializable value (e.g. a multi-MB list) would flood the wire and
    # can OOM the parent — the output-size cap must bound the RESULT too, not just
    # stdout/stderr. Past the cap, hand back a truncated repr marker instead.
    if len(serialized) > cap:
        return serialized[:cap] + f"… [result truncated: {len(serialized)} bytes > {cap}-byte cap]"
    return value


def execute_code(code: str, globals_ns: dict, fuel: dict) -> dict:
    """Execute ONE action in the persistent namespace and return an exec_result
    body dict (WITHOUT ``type``/``id``, which the loop stamps).

    Captures stdout/stderr into capped buffers, evaluates a trailing bare
    expression as ``result``, and converts an uncaught ``Exception`` (NOT
    ``BaseException`` — KeyboardInterrupt/SystemExit propagate to kill the
    process so the parent resets) into an ok:false body. The kernel stays alive
    after a normal exception.
    """
    max_output = int(fuel.get("max_output", 65536))
    out = _CappedStringIO(max_output)
    err = _CappedStringIO(max_output)
    result_val: Any = None
    t0 = time.monotonic()
    ok = True
    error_str = ""
    tb_str = ""

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            module, last_expr = _split_last_expr(code)
            body_code = compile(module, "<codeact>", "exec")
            exec(body_code, globals_ns, globals_ns)
            if last_expr is not None:
                expr_code = compile(last_expr, "<codeact>", "eval")
                result_val = eval(expr_code, globals_ns, globals_ns)
        except MemoryError:
            # The RLIMIT_AS cap fired. Surface a clean, explicit message; the
            # allocator may be wedged, so the parent is recommended to reset.
            ok = False
            error_str = "MemoryError: memory fuel exhausted"
            tb_str = _capped_tb(max_output)
        except Exception as e:  # noqa: BLE001 — recoverable user-code failure
            ok = False
            error_str = f"{type(e).__name__}: {e}"
            tb_str = _capped_tb(max_output)

    wall_ms = int((time.monotonic() - t0) * 1000)
    stdout_s = out.getvalue()
    stderr_s = err.getvalue()
    out_bytes = len(stdout_s.encode("utf-8")) + len(stderr_s.encode("utf-8"))
    fuel_out = {"wall_ms": wall_ms, "out_bytes": out_bytes}

    if ok:
        body = {
            "ok": True,
            "stdout": stdout_s,
            "stderr": stderr_s,
            "result": _serialize_result(result_val, max_output),
            "truncated": bool(out.truncated or err.truncated),
            "fuel": fuel_out,
        }
    else:
        body = {
            "ok": False,
            "error": error_str,
            "traceback": tb_str,
            "stdout": stdout_s,
            "stderr": stderr_s,
            "result": None,
            "fuel": fuel_out,
        }
    return body


def _capped_tb(max_output: int) -> str:
    return traceback.format_exc()[: max(0, int(max_output))]


def run_request_loop(stdin, stdout, *, mem_status: str = "unsupported") -> None:
    """The kernel's main read loop. Reads NDJSON requests from ``stdin`` and
    writes NDJSON responses to ``stdout``.

    Servicing model:
      - ``exec``: run the code; a bridge stub mid-exec writes a ``tool_call`` and
        BLOCKS on ``_recv`` for its ``tool_result`` (read off the SAME stdin) —
        synchronous request/response, at most one outstanding tool_call.
      - ``tool_result`` arriving OUTSIDE an exec (no awaiter) is ignored.
      - ``ping`` → ``pong``. Unknown types → diagnostic to stderr, ignored.
      - EOF on stdin → clean exit.

    This function is importable for tests: pass in-memory pipes and drive it.
    """

    def _send(obj: dict) -> None:
        stdout.write(P.encode(obj))
        stdout.flush()

    # State the bridge closures read: the id of the exec currently running and the
    # call_id its stub is awaiting. Held in a dict so nested closures can mutate it.
    state = {"exec_id": None, "awaiting": None}

    def _recv(call_id: str) -> tuple:
        """Block reading stdin for the tool_result matching ``call_id``.

        A tool_result with a MISMATCHED call_id is a protocol error surfaced into
        user code as a RuntimeError (→ ok:false exec_result; the parent does not
        reset on that alone). EOF mid-wait raises so the exec ends and the process
        exits (parent sees the crash and resets).
        """
        state["awaiting"] = call_id
        try:
            while True:
                line = stdin.readline()
                if line == "":
                    raise RuntimeError("kernel stdin closed while awaiting tool_result")
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = P.decode(line)
                except (ValueError, json.JSONDecodeError) as e:
                    raise RuntimeError(f"malformed tool_result line: {e}")
                if msg.get("type") != P.TYPE_TOOL_RESULT:
                    # Only tool_result is legal while a stub blocks; anything else
                    # is a desync.
                    raise RuntimeError(
                        f"expected tool_result while awaiting {call_id}, "
                        f"got type={msg.get('type')!r}")
                if msg.get("call_id") != call_id:
                    raise RuntimeError(
                        f"tool_result call_id {msg.get('call_id')!r} does not match "
                        f"awaited {call_id!r}")
                if msg.get("ok"):
                    return True, msg.get("result")
                return False, msg.get("error", "tool call failed")
        finally:
            state["awaiting"] = None

    globals_ns = build_globals(_send, _recv, lambda: state["exec_id"])

    # READY handshake (sent once, before the first exec is serviced).
    _send(P.ready(os.getpid(), sys.version))
    if mem_status == "unsupported":
        # Report the no-op exactly once via stderr diagnostic so the wire stays clean.
        sys.stderr.write("[codeact] memory limit unsupported on this platform\n")
        sys.stderr.flush()

    while True:
        line = stdin.readline()
        if line == "":
            break  # EOF — parent closed stdin; exit cleanly.
        line = line.strip()
        if not line:
            continue
        try:
            req = P.decode(line)
        except (ValueError, json.JSONDecodeError) as e:
            sys.stderr.write(f"[codeact] dropping malformed request line: {e}\n")
            sys.stderr.flush()
            continue

        rtype = req.get("type")
        if rtype == P.TYPE_EXEC:
            exec_id = req.get("id")
            state["exec_id"] = exec_id
            fuel = req.get("fuel") or {}
            body = execute_code(req.get("code", ""), globals_ns, fuel)
            state["exec_id"] = None
            body["type"] = P.TYPE_EXEC_RESULT
            body["id"] = exec_id
            if mem_status != "ok":
                body["mem_limit"] = "unsupported" if mem_status == "unsupported" else "error"
            _send(body)
        elif rtype == P.TYPE_PING:
            _send(P.pong(req.get("id")))
        elif rtype == P.TYPE_TOOL_RESULT:
            # A stray tool_result with no awaiter (e.g. a late answer for a killed
            # exec). Drop it — the matching recv loop is the only legal consumer.
            sys.stderr.write("[codeact] dropping unsolicited tool_result\n")
            sys.stderr.flush()
        else:
            sys.stderr.write(f"[codeact] unknown request type: {rtype!r}\n")
            sys.stderr.flush()


def main() -> None:
    """Process entrypoint: ``python -u -m src.codeact.kernel_main``."""
    try:
        mem_mb = int(os.environ.get("KORGEX_CODEACT_MEM_MB", "1024"))
    except (TypeError, ValueError):
        mem_mb = 1024
    mem_status = _apply_mem_limit(mem_mb)

    # ISOLATE THE PROTOCOL CHANNEL FROM fd 1 (CRITICAL). User code — or a subprocess
    # it spawns, or a C extension, or os.write(1, ...) — writing RAW bytes to fd 1
    # must NOT corrupt the NDJSON wire or hang the parent. So: dup the original fd 1
    # to a PRIVATE protocol stream (still wired to the parent's read pipe), then point
    # fd 1 at fd 2 (stderr) so any raw fd-1 write lands in the parent's drained stderr
    # buffer instead of the protocol. Python-level prints during exec are captured by
    # redirect_stdout; outside exec they now go to stderr (never the wire).
    protocol_fd = os.dup(1)
    protocol_out = os.fdopen(protocol_fd, "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    run_request_loop(sys.stdin, protocol_out, mem_status=mem_status)


if __name__ == "__main__":
    main()
