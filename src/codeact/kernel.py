"""PERSISTENT FUEL-METERED KERNEL — the parent-side handle.

``KernelHandle`` owns the kernel subprocess and exposes the full per-exec
round-trip: ``.exec(code, fuel, on_tool_call)`` writes an exec request, runs the
deadline-guarded read loop, services every ``tool_call`` RPC via the injected
``on_tool_call`` callback (the Agent's governed bridge), and returns the terminal
``exec_result`` dict. Construction is LAZY (the child is spawned on first
``.exec``). A timeout or crash is RECOVERABLE: the handle KILLS the child, marks
itself dead (lazy respawn on the next ``.exec``), and returns a synthesized error
dict — it NEVER raises into the agent loop and NEVER hangs.

Wall-time fuel is the parent's single source of truth: it bounds each readline by
``select`` on POSIX (or a reader-thread + ``Event`` fallback on non-POSIX, since
Windows can't ``select`` on a pipe). The kernel deliberately has no SIGALRM — an
alarm would misfire while the kernel legitimately blocks on a bridged tool call.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import protocol as P


def _env_int(name: str, default: int) -> int:
    """Read an int env knob in-process (idiom matches agent.py: int(os.environ.get(...)))."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def resolve_fuel() -> dict:
    """Resolve the {wall_ms, mem_mb, max_output} fuel block from KORGEX_CODEACT_*.

    Read here (parent side) AND passed in the exec request so the kernel and
    parent agree on the same knobs.
    """
    return {
        "wall_ms": _env_int("KORGEX_CODEACT_FUEL_MS", 30000),
        "mem_mb": _env_int("KORGEX_CODEACT_MEM_MB", 1024),
        "max_output": _env_int("KORGEX_CODEACT_MAX_OUTPUT", 65536),
    }


_IS_POSIX = os.name == "posix"
if _IS_POSIX:
    import select


class KernelHandle:
    """Parent-side handle to a persistent CodeAct kernel subprocess."""

    def __init__(self, repo_root: Optional[str] = None,
                 python_exe: Optional[str] = None):
        self.repo_root = repo_root or os.getcwd()
        self._python = python_exe or sys.executable
        self._proc: Optional[subprocess.Popen] = None
        # Non-POSIX fallback plumbing (lazily created on spawn): a daemon thread
        # drains stdout into a queue so the read loop can block with a timeout.
        self._reader_thread: Optional[threading.Thread] = None
        self._line_q: Optional["queue.Queue[Optional[str]]"] = None
        self._mem_limit_note: Optional[str] = None
        # stderr is drained CONTINUOUSLY by a daemon thread into a bounded buffer.
        # Two reasons: (1) a blocking proc.stderr.read() on a LIVE kernel hangs the
        # parent forever (the C3 hang); (2) an undrained stderr pipe fills (~64KB)
        # and DEADLOCKS the child when user code/subprocess writes a lot to it (and
        # raw fd-1 writes are routed here by the kernel's fd isolation).
        self._stderr_buf: Optional["collections.deque[str]"] = None
        self._stderr_thread: Optional[threading.Thread] = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _spawn(self) -> None:
        """Launch the kernel subprocess and consume its READY line."""
        # The kernel's cwd is the WORKSPACE root (so code's relative paths resolve
        # against the worktree the agent is editing). But `-m src.codeact.kernel_main`
        # must import the korgex PACKAGE, which lives at the install root — NOT under
        # an arbitrary workspace. So we put the install root on the child's
        # PYTHONPATH explicitly; otherwise the kernel dies at import ("exited before
        # READY") for every repo_root that isn't the korgex checkout itself (the
        # common case: a worktree or any tmp workspace). cwd alone is NOT enough.
        env = dict(os.environ)
        install_root = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            install_root + os.pathsep + existing if existing else install_root)
        self._proc = subprocess.Popen(
            [self._python, "-u", "-m", "src.codeact.kernel_main"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=self.repo_root,
            env=env,
        )
        # Always drain stderr into a bounded buffer so it can never fill + deadlock,
        # and so _stderr_tail never blocks on a live process.
        self._stderr_buf = collections.deque(maxlen=400)
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(self._proc.stderr, self._stderr_buf),
            daemon=True)
        self._stderr_thread.start()
        if not _IS_POSIX:
            self._line_q = queue.Queue()
            self._reader_thread = threading.Thread(
                target=self._drain_stdout, args=(self._proc.stdout, self._line_q),
                daemon=True)
            self._reader_thread.start()
        # Consume the READY handshake (bounded by a generous boot deadline). A
        # kernel that never readies is treated as a crash.
        deadline = time.monotonic() + 10.0
        while True:
            line = self._readline_until(deadline)
            if line is None:
                self._kill()
                raise RuntimeError("kernel failed to send READY before boot deadline")
            if line == "":
                self._kill()
                raise RuntimeError("kernel exited before READY")
            try:
                msg = P.decode(line)
            except (ValueError, json.JSONDecodeError):
                continue  # ignore any pre-READY noise defensively
            if msg.get("type") == P.TYPE_READY:
                return

    @staticmethod
    def _drain_stdout(stream, q: "queue.Queue") -> None:
        """Non-POSIX reader thread: push each stdout line (then a final None at EOF)."""
        try:
            for line in iter(stream.readline, ""):
                q.put(line)
        finally:
            q.put(None)

    @staticmethod
    def _drain_stderr(stream, buf: "collections.deque") -> None:
        """Daemon thread: continuously drain stderr into a bounded ring buffer.

        Reads until EOF (the child's stderr closing on death), so the pipe never
        fills and we never block on a read of a live process. Bounded, so a chatty
        kernel can't grow memory without limit.
        """
        try:
            for line in iter(stream.readline, ""):
                buf.append(line)
        except (OSError, ValueError):
            pass

    def reset(self) -> None:
        """Kill the child and drop the handle so the next .exec respawns lazily."""
        self._kill()

    def _kill(self) -> None:
        """SIGTERM → wait(2) → SIGKILL escalation (mirrors cli.py's stop path)."""
        proc = self._proc
        self._proc = None
        self._reader_thread = None
        self._line_q = None
        self._stderr_thread = None
        self._stderr_buf = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
        except (OSError, ValueError):
            pass
        finally:
            for s in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if s is not None:
                        s.close()
                except (OSError, ValueError):
                    pass

    # ── low-level wire I/O ─────────────────────────────────────────────────────
    def _write(self, obj: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(P.encode(obj))
        self._proc.stdin.flush()

    def _readline_until(self, deadline: float) -> Optional[str]:
        """Read one stdout line, blocking until ``deadline`` (monotonic seconds).

        Returns the line (``""`` on EOF) or ``None`` if the deadline passed with
        no line available. POSIX uses ``select`` on the pipe; elsewhere we poll
        the reader-thread queue.
        """
        assert self._proc is not None and self._proc.stdout is not None
        if _IS_POSIX:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                return None
            return self._proc.stdout.readline()
        # Non-POSIX: pull from the drain queue with a timeout.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            item = self._line_q.get(timeout=remaining)  # type: ignore[union-attr]
        except queue.Empty:
            return None
        return "" if item is None else item

    def _stderr_tail(self, limit: int = 2000) -> str:
        """Best-effort drained stderr for crash diagnostics (never parsed for protocol).

        Reads from the bounded buffer the drainer thread fills — NEVER a blocking
        ``proc.stderr.read()`` on a possibly-live kernel (that was the C3 hang).
        """
        buf = self._stderr_buf
        if not buf:
            return ""
        return "".join(buf)[-limit:]

    # ── the per-exec round-trip ────────────────────────────────────────────────
    def exec(self, code: str, fuel: dict,
             on_tool_call: Callable[[str, dict], dict]) -> dict:
        """Run ONE action through the full protocol round-trip and return the
        terminal result dict. NEVER raises; on timeout/crash it kills+resets the
        kernel and returns a synthesized ``{"error": ..., "fuel": {...}}`` dict.

        ``on_tool_call(name, args) -> dict`` is the parent's GOVERNED executor
        (Agent._bridge_tool_call bound with the code action's seq). Its return
        value is sent back into the kernel verbatim as the tool's result.
        """
        wall_ms = int(fuel.get("wall_ms", 30000))
        if not self.alive:
            try:
                self._spawn()
            except Exception as e:  # noqa: BLE001 — spawn failure is recoverable
                self._kill()
                return {"error": f"kernel failed to start: {e}",
                        "fuel": {"wall_ms": wall_ms}, "stdout": "", "stderr": ""}

        exec_id = _new_id()
        try:
            self._write(P.exec_request(exec_id, code, fuel))
        except (OSError, ValueError, BrokenPipeError) as e:
            tail = self._stderr_tail()
            self._kill()
            return {"error": f"kernel crashed before exec: {e}; {tail}".strip(),
                    "fuel": {"wall_ms": wall_ms}, "stdout": "", "stderr": ""}

        started = time.monotonic()
        deadline = started + (wall_ms / 1000.0)
        partial_stdout = ""

        while True:
            try:
                line = self._readline_until(deadline)
            except (OSError, ValueError) as e:
                self._kill()
                return {"error": f"kernel read error: {e}",
                        "fuel": {"wall_ms": wall_ms}, "stdout": partial_stdout, "stderr": ""}

            if line is None:
                # Deadline elapsed with no terminal result → KILL + reset. Report the
                # ACTUAL kernel wall-clock elapsed (not the budget) so the trace/ledger
                # is truthful about the runaway fuel exists to surface.
                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._kill()
                return {
                    "error": f"kernel exec timed out (~{elapsed_ms}ms compute, "
                             f"budget {wall_ms}ms); kernel reset",
                    "fuel": {"wall_ms": wall_ms, "elapsed_ms": elapsed_ms},
                    "stdout": partial_stdout,
                }
            if line == "":
                # EOF mid-exec → the kernel died.
                tail = self._stderr_tail()
                self._kill()
                return {"error": f"kernel crashed: {tail}".strip(),
                        "fuel": {"wall_ms": wall_ms}, "stdout": partial_stdout, "stderr": ""}

            stripped = line.strip()
            if not stripped:
                continue
            try:
                msg = P.decode(stripped)
            except (ValueError, json.JSONDecodeError) as e:
                # Malformed line == contaminated wire → treat as a crash + reset.
                tail = self._stderr_tail()
                self._kill()
                return {"error": f"kernel protocol error (bad JSON line): {e}; {tail}".strip(),
                        "fuel": {"wall_ms": wall_ms}, "stdout": partial_stdout, "stderr": ""}

            mtype = msg.get("type")
            if mtype == P.TYPE_TOOL_CALL:
                # Service the bridged call through the parent's governed executor,
                # then write the answer back; KEEP looping (do NOT return yet).
                name = msg.get("name")
                args = msg.get("args") or {}
                call_id = msg.get("call_id")
                tool_t0 = time.monotonic()
                try:
                    result = on_tool_call(name, args)
                    self._write(P.tool_result_ok(call_id, result))
                except Exception as e:  # noqa: BLE001 — bridge failure → tell the kernel
                    try:
                        self._write(P.tool_result_err(call_id, f"{type(e).__name__}: {e}"))
                    except (OSError, ValueError, BrokenPipeError):
                        tail = self._stderr_tail()
                        self._kill()
                        return {"error": f"kernel crashed mid tool_result: {tail}".strip(),
                                "fuel": {"wall_ms": wall_ms}, "stdout": partial_stdout,
                                "stderr": ""}
                # wall_ms bounds KERNEL COMPUTE only — the parent's tool-servicing
                # time (route_tool_call + ledger + compression) must NOT count against
                # it, or one slow tool would falsely time the kernel out and wipe
                # session state. Push the deadline out by the round-trip's duration.
                # (A genuinely hung tool is bounded by that tool's own timeout.)
                deadline += time.monotonic() - tool_t0
                continue
            if mtype == P.TYPE_EXEC_RESULT:
                if msg.get("id") != exec_id:
                    # A stray result for a different exec — ignore and keep reading.
                    continue
                # The ONLY message type that terminates the per-exec loop.
                # Strip the framing fields; hand the body up to the agent.
                msg.pop("type", None)
                # A MemoryError leaves the allocator possibly wedged → reset the
                # kernel so the next action gets a clean process (state is lost,
                # which the error message already makes explicit to the model).
                if msg.get("ok") is False and "MemoryError" in str(msg.get("error", "")):
                    self._kill()
                return msg
            if mtype in (P.TYPE_READY, P.TYPE_PONG):
                continue  # liveness chatter — ignore
            # Unknown type on the wire → protocol error → reset.
            tail = self._stderr_tail()
            self._kill()
            return {"error": f"kernel sent unknown message type {mtype!r}; {tail}".strip(),
                    "fuel": {"wall_ms": wall_ms}, "stdout": partial_stdout, "stderr": ""}


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex
