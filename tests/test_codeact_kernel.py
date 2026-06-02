"""Tests for the PERSISTENT fuel-metered CodeAct kernel (src/codeact).

These exercise the kernel through its real subprocess + the parent handle, plus
the child-runner logic in-process (importable WITHOUT the agent). Coverage:
  - exec returns stdout + the last-expression value
  - state PERSISTS across two execs (the "code as action space WITH memory" guarantee)
  - an infinite loop is KILLED by the parent's wall-time fuel
  - a memory bomb is capped via RLIMIT_AS (skipped cleanly where unenforceable)
  - output is capped (truncated flag set, byte budget honored)
  - a crash is recovered: the next exec respawns the kernel
  - the bridged tool-call round-trip works against a stubbed parent
  - protocol details: ready handshake, ping/pong, mismatched call_id, repr fallback

All run fully OFFLINE — no provider, no network, no agent.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from src.codeact import KernelHandle, resolve_fuel
from src.codeact import protocol as P
from src.codeact import kernel_main as KM

REPO_ROOT = str(__import__("pathlib").Path(__file__).resolve().parents[1])

FAST_FUEL = {"wall_ms": 8000, "mem_mb": 1024, "max_output": 65536}


def _no_tools(name, args):
    return {"error": f"no tool {name} in this test"}


@pytest.fixture()
def kernel():
    k = KernelHandle(repo_root=REPO_ROOT)
    try:
        yield k
    finally:
        k.reset()


def _rlimit_as_enforceable() -> bool:
    """True iff a child can set RLIMIT_AS AND have a big alloc raise MemoryError.

    POSIX alone is not enough: macOS has RLIMIT_AS but rejects setrlimit
    ("current limit exceeds maximum limit"), so the cap is a no-op there. We probe
    a throwaway child to decide whether the memory-bomb assertion is meaningful.
    """
    probe = (
        "import resource,sys\n"
        "try:\n"
        " resource.setrlimit(resource.RLIMIT_AS,(128*1024*1024,128*1024*1024))\n"
        "except Exception:\n"
        " print('UNSUPPORTED'); sys.exit(0)\n"
        "try:\n"
        " b=bytearray(512*1024*1024); print('NOTENFORCED')\n"
        "except MemoryError:\n"
        " print('ENFORCED')\n"
    )
    try:
        out = subprocess.run([sys.executable, "-c", probe],
                             capture_output=True, text=True, timeout=20)
    except Exception:
        return False
    return out.stdout.strip() == "ENFORCED"


# ── exec returns stdout + last-expression value ───────────────────────────────
def test_exec_returns_stdout_and_value(kernel):
    r = kernel.exec("print('hello world')\n40 + 2", FAST_FUEL, _no_tools)
    assert r["ok"] is True
    assert r["stdout"] == "hello world\n"
    assert r["stderr"] == ""
    assert r["result"] == 42
    assert r["truncated"] is False
    assert "wall_ms" in r["fuel"] and "out_bytes" in r["fuel"]


def test_statement_only_has_null_result(kernel):
    r = kernel.exec("z = 99", FAST_FUEL, _no_tools)
    assert r["ok"] is True
    assert r["result"] is None


def test_non_json_result_falls_back_to_repr(kernel):
    r = kernel.exec("object()", FAST_FUEL, _no_tools)
    assert r["ok"] is True
    assert isinstance(r["result"], str)
    assert r["result"].startswith("<object object")


# ── state PERSISTS across two execs ───────────────────────────────────────────
def test_state_persists_across_execs(kernel):
    r1 = kernel.exec("import math\ncounter = 10\ndef inc():\n    global counter\n    counter += 1\n    return counter", FAST_FUEL, _no_tools)
    assert r1["ok"] is True
    # Second exec sees the var, the import, AND the function defined in the first.
    r2 = kernel.exec("inc()\ninc()\nmath.floor(counter + 0.5)", FAST_FUEL, _no_tools)
    assert r2["ok"] is True
    assert r2["result"] == 12  # 10 -> 11 -> 12


# ── an infinite loop is KILLED by the wall-time fuel ──────────────────────────
def test_infinite_loop_killed_by_wall_fuel(kernel):
    fuel = {"wall_ms": 600, "mem_mb": 1024, "max_output": 65536}
    t0 = time.monotonic()
    r = kernel.exec("while True:\n    pass", fuel, _no_tools)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert "timed out" in r["error"]
    assert "reset" in r["error"]
    assert r["fuel"]["wall_ms"] == 600
    # Killed promptly (generous ceiling for slow CI), and the kernel is reset.
    assert elapsed_ms < 5000
    assert kernel.alive is False
    # And it recovers on the next exec (state lost, fresh process).
    r2 = kernel.exec("1 + 1", fuel, _no_tools)
    assert r2["ok"] is True and r2["result"] == 2


# ── a memory bomb is capped (skip cleanly if RLIMIT_AS unenforceable) ─────────
def test_memory_bomb_is_capped(monkeypatch):
    # REGRESSION (adversarial verify, HIGH): the old test passed fuel mem_mb=128 and
    # allocated 512MB expecting MemoryError — but per-exec mem_mb is NOT honored (the
    # RLIMIT_AS cap is set ONCE at kernel BOOT from KORGEX_CODEACT_MEM_MB, which is not
    # portably re-tightenable). With the default 1024MB cap, 512MB does NOT raise on
    # Linux (the deploy platform), so the test was VACUOUS there — green only because
    # it skipped on the dev's macOS. Set a small BOOT cap, allocate well above it.
    if not _rlimit_as_enforceable():
        pytest.skip("RLIMIT_AS not enforceable on this platform (e.g. macOS) — no-op cap")
    monkeypatch.setenv("KORGEX_CODEACT_MEM_MB", "128")  # the kernel reads this at boot
    k = KernelHandle(repo_root=REPO_ROOT)
    try:
        fuel = {"wall_ms": 10000, "max_output": 65536}
        r = k.exec("x = bytearray(512 * 1024 * 1024)\nlen(x)", fuel, _no_tools)
        assert r["ok"] is False
        assert "MemoryError" in r["error"]
        # The parent resets the kernel after a MemoryError (clean allocator next action).
        assert k.alive is False
        r2 = k.exec("2 + 2", fuel, _no_tools)
        assert r2["ok"] is True and r2["result"] == 4
    finally:
        k.reset()


def test_memory_limit_reported_when_unsupported(kernel):
    # Whatever the platform, the kernel reports a mem_limit field unless the cap
    # was applied cleanly ("ok"). On macOS this is "error"; on Windows "unsupported".
    r = kernel.exec("1", FAST_FUEL, _no_tools)
    assert r["ok"] is True
    if not _rlimit_as_enforceable():
        assert r.get("mem_limit") in ("unsupported", "error")


# ── output is capped ──────────────────────────────────────────────────────────
def test_output_is_capped(kernel):
    fuel = {"wall_ms": 5000, "mem_mb": 1024, "max_output": 100}
    r = kernel.exec("print('A' * 5000)", fuel, _no_tools)
    assert r["ok"] is True
    assert r["truncated"] is True
    # The captured stdout never exceeds the byte budget.
    assert len(r["stdout"].encode("utf-8")) <= 100


def test_output_cap_combined_streams(kernel):
    fuel = {"wall_ms": 5000, "mem_mb": 1024, "max_output": 50}
    r = kernel.exec("import sys\nprint('x'*200)\nsys.stderr.write('y'*200)", fuel, _no_tools)
    assert r["truncated"] is True
    assert len(r["stdout"].encode("utf-8")) <= 50
    assert len(r["stderr"].encode("utf-8")) <= 50


# ── a crash is recovered by reset ─────────────────────────────────────────────
def test_crash_is_recovered(kernel):
    r = kernel.exec("import os\nos._exit(3)", FAST_FUEL, _no_tools)
    assert "error" in r
    assert "crashed" in r["error"]
    assert kernel.alive is False
    # The very next exec lazily respawns the kernel and works.
    r2 = kernel.exec("'recovered'", FAST_FUEL, _no_tools)
    assert r2["ok"] is True
    assert r2["result"] == "recovered"
    assert kernel.alive is True


def test_user_exception_is_recoverable_without_reset(kernel):
    # A NORMAL exception in user code is ok:false but the kernel STAYS ALIVE
    # (no reset needed) and prior state survives.
    kernel.exec("kept = 7", FAST_FUEL, _no_tools)
    r = kernel.exec("raise ValueError('boom')", FAST_FUEL, _no_tools)
    assert r["ok"] is False
    assert r["error"] == "ValueError: boom"
    assert "Traceback" in r["traceback"]
    assert kernel.alive is True
    r2 = kernel.exec("kept * 2", FAST_FUEL, _no_tools)
    assert r2["result"] == 14  # state survived the exception


# ── the tool-request round-trip works against a stubbed parent ────────────────
def test_tool_round_trip_against_stub_parent(kernel):
    seen = []

    def stub_parent(name, args):
        seen.append((name, args))
        if name == "Read":
            return {"content": "print('hi')\n", "size": 321}
        if name == "Bash":
            return {"stdout": "listing", "stderr": "", "exit_code": 0}
        return {"error": f"unexpected {name}"}

    code = (
        "f = read_file('a.py')\n"
        "b = bash('ls -la')\n"
        "print('size is', f['size'])\n"
        "f['size'] + b['exit_code']\n"
    )
    r = kernel.exec(code, FAST_FUEL, stub_parent)
    assert r["ok"] is True
    assert r["result"] == 321
    assert r["stdout"] == "size is 321\n"
    # The parent saw BOTH calls, name-mapped + param-mapped to the user-facing schema.
    assert seen == [
        ("Read", {"file_path": "a.py"}),
        ("Bash", {"command": "ls -la"}),
    ]


def test_tool_failure_surfaces_as_runtime_error(kernel):
    def failing_parent(name, args):
        raise ValueError("gate blocked this call")

    r = kernel.exec("read_file('secret')", FAST_FUEL, failing_parent)
    assert r["ok"] is False
    # The bridge raises RuntimeError(error) into user code → recoverable exec_result.
    assert "RuntimeError" in r["error"]
    assert "gate blocked this call" in r["error"]
    assert kernel.alive is True  # a tool failure is NOT a kernel crash


def test_call_tool_escape_hatch(kernel):
    def stub_parent(name, args):
        return {"routed": name, "got": args}

    r = kernel.exec("call_tool('browser_navigate', url='http://example.com')",
                    FAST_FUEL, stub_parent)
    assert r["ok"] is True
    assert r["result"] == {"routed": "browser_navigate", "got": {"url": "http://example.com"}}


def test_multiple_tool_calls_distinct_call_ids(kernel):
    # Two calls in one exec must each get a fresh call_id and match correctly —
    # values must not cross. The stub returns the arg it got so a swap is visible.
    def echo_parent(name, args):
        return {"echo": args.get("file_path")}

    code = (
        "a = read_file('first.py')\n"
        "b = read_file('second.py')\n"
        "(a['echo'], b['echo'])\n"
    )
    r = kernel.exec(code, FAST_FUEL, echo_parent)
    assert r["result"] == ["first.py", "second.py"]


# ── protocol-level details ────────────────────────────────────────────────────
def test_ready_handshake_and_pid_and_alive(kernel):
    # First exec implicitly forces the spawn + READY consumption; alive is True.
    kernel.exec("1", FAST_FUEL, _no_tools)
    assert kernel.alive is True


def test_ping_pong_is_ignored_by_exec_loop(kernel):
    # ping/pong are liveness chatter the exec loop skips; an exec still returns
    # its terminal result cleanly even if pongs are interleaved. Here we just send
    # a ping directly and confirm a pong comes back, then a normal exec still works.
    kernel.exec("1", FAST_FUEL, _no_tools)  # ensure spawned
    kernel._write(P.ping("PING1"))
    # Drain until we see the matching pong (bounded).
    deadline = time.monotonic() + 3.0
    saw_pong = False
    while time.monotonic() < deadline:
        line = kernel._readline_until(deadline)
        if not line:
            break
        msg = P.decode(line.strip())
        if msg.get("type") == P.TYPE_PONG and msg.get("id") == "PING1":
            saw_pong = True
            break
    assert saw_pong
    # The kernel is still healthy for a real exec afterward.
    r = kernel.exec("123", FAST_FUEL, _no_tools)
    assert r["result"] == 123


# ── child-runner is importable + unit-testable WITHOUT the agent or subprocess ─
def test_execute_code_in_process_no_subprocess():
    # build_globals with stub send/recv → execute_code directly. Proves the
    # child-runner logic is decoupled from any real pipe/agent.
    sent = []

    def send(obj):
        sent.append(obj)

    def recv(call_id):
        return True, {"size": 11}

    g = KM.build_globals(send, recv, lambda: "EXEC-X")
    body = KM.execute_code("v = read_file('z.py')\nprint(v['size'])\nv['size'] + 1",
                           g, {"max_output": 65536})
    assert body["ok"] is True
    assert body["result"] == 12
    assert body["stdout"] == "11\n"
    # The stub send saw a tool_call stamped with the current exec id.
    assert len(sent) == 1
    assert sent[0]["type"] == P.TYPE_TOOL_CALL
    assert sent[0]["exec_id"] == "EXEC-X"
    assert sent[0]["name"] == "Read"
    assert sent[0]["args"] == {"file_path": "z.py"}


def test_raw_fd1_write_does_not_hang_or_wipe_state(kernel):
    # REGRESSION (adversarial verify, CRITICAL C3): user code writing RAW bytes to
    # fd 1 — os.write(1, ...), or a subprocess inheriting it — used to corrupt the
    # NDJSON protocol and HANG the parent forever (_stderr_tail's blocking read on a
    # live kernel), or at best wipe session state with a spurious reset. The protocol
    # channel must be isolated from fd 1, so raw fd-1 writes are harmless.
    kernel.exec("keep = 'survivor'", FAST_FUEL, _no_tools)
    t0 = time.monotonic()
    r = kernel.exec("import os\nos.write(1, b'raw not-json bytes\\n')\n40 + 2",
                    FAST_FUEL, _no_tools)
    assert (time.monotonic() - t0) < 6  # did NOT hang
    assert r.get("ok") is True and r.get("result") == 42  # protocol intact
    # a subprocess writing to stdout must also be harmless (no corruption/hang)
    r2 = kernel.exec("import subprocess\nsubprocess.run(['printf', 'hi'])\n7 * 7",
                     FAST_FUEL, _no_tools)
    assert r2.get("ok") is True and r2.get("result") == 49
    # and session state survived (no spurious reset)
    r3 = kernel.exec("keep", FAST_FUEL, _no_tools)
    assert r3.get("result") == "survivor"


def test_slow_tool_does_not_consume_kernel_wall_fuel(kernel):
    # REGRESSION (adversarial verify, CRITICAL C1): the wall-time deadline counted the
    # PARENT's tool-servicing time, so ONE slow tool falsely "timed out" and wiped
    # session state. wall_ms must bound KERNEL COMPUTE only; parent tool-servicing
    # time is excluded (tools carry their own timeouts).
    def slow_parent(name, args):
        time.sleep(1.0)  # >> the kernel compute budget below
        return {"ok": True}

    fuel = {"wall_ms": 400, "mem_mb": 1024, "max_output": 65536}  # 0.4s compute budget
    r = kernel.exec("x = read_file('a')\n'done'", fuel, slow_parent)
    assert r.get("ok") is True, r          # NOT a false timeout
    assert r.get("result") == "done"
    assert kernel.alive is True            # NOT killed/reset by parent tool time


def test_capped_stringio_byte_budget():
    buf = KM._CappedStringIO(5)
    buf.write("abc")
    buf.write("defgh")  # only 2 bytes fit
    assert buf.truncated is True
    assert len(buf.getvalue().encode("utf-8")) <= 5


def test_split_last_expr_handles_trailing_expr_and_statements():
    mod, expr = KM._split_last_expr("a = 1\nb = 2\na + b")
    assert expr is not None  # trailing bare expression captured for eval
    mod2, expr2 = KM._split_last_expr("a = 1\nb = 2")
    assert expr2 is None  # statement-only → no eval, result stays None


def test_resolve_fuel_reads_env_knobs(monkeypatch):
    monkeypatch.setenv("KORGEX_CODEACT_FUEL_MS", "1234")
    monkeypatch.setenv("KORGEX_CODEACT_MEM_MB", "256")
    monkeypatch.setenv("KORGEX_CODEACT_MAX_OUTPUT", "999")
    assert resolve_fuel() == {"wall_ms": 1234, "mem_mb": 256, "max_output": 999}
    # Garbage falls back to the documented defaults (int-cast idiom is guarded).
    monkeypatch.setenv("KORGEX_CODEACT_FUEL_MS", "not-an-int")
    assert resolve_fuel()["wall_ms"] == 30000


def test_mismatched_call_id_surfaces_as_exec_error():
    # If the parent answers with the WRONG call_id, the kernel raises into user
    # code (RuntimeError → ok:false), and does NOT silently accept it. Driven
    # in-process via recv that returns through a mismatched-id path is awkward, so
    # exercise the loop's recv contract via run_request_loop with crafted pipes.
    import io
    import threading

    # Parent → kernel stdin: one exec that calls read_file, then we feed a
    # tool_result with a BAD call_id. Kernel → out captured.
    class _Pipe:
        def __init__(self):
            self._q = []
            self._cv = threading.Condition()
            self._closed = False

        def feed(self, s):
            with self._cv:
                self._q.append(s)
                self._cv.notify_all()

        def close(self):
            with self._cv:
                self._closed = True
                self._cv.notify_all()

        def readline(self):
            with self._cv:
                while not self._q and not self._closed:
                    self._cv.wait()
                if self._q:
                    return self._q.pop(0)
                return ""

    stdin = _Pipe()
    out = io.StringIO()
    out_lines = []
    real_write = out.write

    def cap_write(s):
        out_lines.append(s)
        return real_write(s)

    out.write = cap_write  # type: ignore[assignment]

    t = threading.Thread(target=KM.run_request_loop, args=(stdin, out),
                         kwargs={"mem_status": "unsupported"}, daemon=True)
    t.start()

    # Send an exec that issues one tool_call.
    stdin.feed(P.encode(P.exec_request("E1", "read_file('a')", {"max_output": 65536})))

    # Wait for the kernel's tool_call to appear, then answer with a WRONG call_id.
    deadline = time.monotonic() + 5.0
    call_id = None
    while time.monotonic() < deadline and call_id is None:
        for ln in list(out_lines):
            try:
                m = P.decode(ln.strip())
            except Exception:
                continue
            if m.get("type") == P.TYPE_TOOL_CALL:
                call_id = m["call_id"]
                break
        time.sleep(0.01)
    assert call_id is not None
    stdin.feed(P.encode(P.tool_result_ok("WRONG-ID", {"x": 1})))

    # The kernel should emit an ok:false exec_result mentioning the mismatch.
    res = None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and res is None:
        for ln in list(out_lines):
            try:
                m = P.decode(ln.strip())
            except Exception:
                continue
            if m.get("type") == P.TYPE_EXEC_RESULT:
                res = m
                break
        time.sleep(0.01)
    stdin.close()
    assert res is not None
    assert res["ok"] is False
    assert "call_id" in res["error"] or "match" in res["error"]
