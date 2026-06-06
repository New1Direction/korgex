"""CodeAct OS-sandbox — the pure wiring + a live macOS check.

CodeAct's kernel is SAME-TRUST as Bash: raw stdlib (open/os/socket/subprocess)
bypasses the governed, ledger-recorded bridge. This OPT-IN sandbox wraps the kernel
subprocess so it can only WRITE inside the workspace and has NO network — forcing
file-mutation + egress through the bridge (which runs in the unsandboxed parent).
Two backends, same guarantees: Linux → bubblewrap, macOS → Seatbelt (sandbox-exec).
OFF by default; FAIL-CLOSED when isolation is requested but unavailable.

The pure-logic tests run on ANY OS (platform + sandbox-tool path are injected). The
Linux confinement is verified by construction (the declarative bwrap flag set) and on
a Linux box; the macOS confinement is additionally LIVE-validated below when running
on darwin (network + outside-write are actually attempted and must be blocked).
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from src.codeact import sandbox as S


def test_isolation_off_by_default(monkeypatch):
    monkeypatch.delenv("KORGEX_CODEACT_ISOLATION", raising=False)
    assert S.isolation_requested() is False


def test_isolation_request_parsing(monkeypatch):
    for v in ("1", "true", "yes", "on", "strict"):
        monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", v)
        assert S.isolation_requested() is True, v
    for v in ("0", "off", "no", ""):
        monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", v)
        assert S.isolation_requested() is False, v


# ── available(): per-platform backend detection ─────────────────────────────

def test_available_on_linux_with_bwrap(monkeypatch):
    monkeypatch.setattr(S.sys, "platform", "linux")
    monkeypatch.setattr(S.shutil, "which", lambda n: "/usr/bin/bwrap")
    ok, why = S.available()
    assert ok is True and why == "bwrap"


def test_unavailable_when_bwrap_missing(monkeypatch):
    monkeypatch.setattr(S.sys, "platform", "linux")
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    ok, why = S.available()
    assert ok is False and "bwrap" in why.lower()


def test_available_on_macos_with_sandbox_exec(monkeypatch):
    monkeypatch.setattr(S.sys, "platform", "darwin")
    monkeypatch.setattr(S.shutil, "which", lambda n: "/usr/bin/sandbox-exec")
    ok, why = S.available()
    assert ok is True and why == "sandbox-exec"


def test_unavailable_when_sandbox_exec_missing(monkeypatch):
    monkeypatch.setattr(S.sys, "platform", "darwin")
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    ok, why = S.available()
    assert ok is False and "sandbox-exec" in why.lower()


def test_unavailable_on_platform_without_a_backend(monkeypatch):
    monkeypatch.setattr(S.sys, "platform", "win32")
    ok, why = S.available()
    assert ok is False and "win32" in why


# ── wrap_command(): dispatches to the platform backend ──────────────────────

def test_wrap_command_uses_bubblewrap_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(S.sys, "platform", "linux")
    argv = ["/usr/bin/python3", "-u", "-m", "src.codeact.kernel_main"]
    out = S.wrap_command(argv, str(tmp_path), bwrap="/usr/bin/bwrap")
    assert out[0] == "/usr/bin/bwrap"
    assert out[-len(argv):] == argv          # the real command runs after `--`, untouched
    assert "--unshare-net" in out            # NO network egress
    assert "--ro-bind" in out                # rest of fs read-only
    assert "--die-with-parent" in out
    ws = str(tmp_path)
    i = out.index("--bind")
    assert out[i + 1] == ws and out[i + 2] == ws   # workspace bound read-write


def test_wrap_command_rebinds_install_root_after_tmpfs_on_linux(monkeypatch, tmp_path):
    # REGRESSION (Linux dogfood): --tmpfs /tmp hides a korgex install located under
    # /tmp (e.g. a CI clone); install_root must be re-bound read-only AFTER the tmpfs.
    monkeypatch.setattr(S.sys, "platform", "linux")
    install, ws = tmp_path / "install", tmp_path / "ws"
    out = S.wrap_command(["python3"], str(ws), str(install), bwrap="/usr/bin/bwrap")
    ir = str(install)
    assert out.index(ir) > out.index("--tmpfs")          # re-bound AFTER the /tmp tmpfs
    assert out[out.index(ir) - 1] == "--ro-bind"


def test_wrap_command_uses_seatbelt_on_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(S.sys, "platform", "darwin")
    argv = ["/usr/bin/python3", "-c", "1"]
    out = S.wrap_command(argv, str(tmp_path), sandbox_exec="/usr/bin/sandbox-exec")
    assert out[0] == "/usr/bin/sandbox-exec"
    assert out[1] == "-p"                     # inline profile
    assert out[-len(argv):] == argv           # the real command runs untouched
    assert "(deny network*)" in out[2]


# ── Seatbelt (SBPL) profile: the macOS confinement, by construction ─────────

def test_seatbelt_profile_denies_network_and_confines_writes(tmp_path):
    prof = S._seatbelt_profile(str(tmp_path))
    assert prof.startswith("(version 1)")
    assert "(deny network*)" in prof          # no egress
    assert "(deny file-write*)" in prof       # writes denied by default...
    assert "(allow file-write*" in prof       # ...then re-opened for:
    # the workspace, realpath-canonicalized (macOS /tmp,/var are symlinks into /private)
    import os
    assert f'(subpath "{os.path.realpath(str(tmp_path))}")' in prof


def test_seatbelt_profile_escapes_quotes_in_paths(monkeypatch):
    # a workspace path containing a double-quote must not break out of the SBPL literal
    monkeypatch.setattr(S.os.path, "realpath", lambda p: '/ws/we"ird')
    prof = S._seatbelt_profile("/ignored")
    assert '/ws/we\\"ird' in prof             # the quote is backslash-escaped


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"),
                    reason="macOS Seatbelt live check needs darwin + sandbox-exec")
def test_seatbelt_live_blocks_network_and_outside_writes(tmp_path):
    """The real proof on macOS: run python under the actual sandbox and confirm it
    still starts, but network egress and writes outside the workspace are denied."""
    def run(code):
        cmd = S.wrap_command([sys.executable, "-c", code], str(tmp_path))
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    assert run("print('ok')").stdout.strip() == "ok"            # reads + exec still work

    net = run("import socket\n"
              "try:\n socket.create_connection(('1.1.1.1',80),3); print('NETOK')\n"
              "except Exception as e: print('BLOCKED', type(e).__name__)\n")
    assert "BLOCKED" in net.stdout and "NETOK" not in net.stdout, net.stdout

    out = run("import os\n"
              "p=os.path.expanduser('~/.korgex_sb_live_probe')\n"
              "try:\n open(p,'w').write('x'); os.remove(p); print('WROTE')\n"
              "except Exception as e: print('BLOCKED', type(e).__name__)\n")
    assert "BLOCKED" in out.stdout and "WROTE" not in out.stdout, out.stdout


# ── fail-closed: requested-but-no-backend refuses to start ───────────────────

def test_kernel_spawn_fails_closed_when_isolation_unavailable(tmp_path, monkeypatch):
    # Requesting isolation on a box with no backend must FAIL CLOSED: the kernel
    # refuses to start rather than run model code unconfined. Force an unsupported
    # platform so this holds regardless of the test host (Linux or macOS).
    monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", "on")
    monkeypatch.setattr(S.sys, "platform", "win32")
    from src.codeact import KernelHandle
    k = KernelHandle(repo_root=str(tmp_path))
    try:
        r = k.exec("1 + 1", {"wall_ms": 4000, "max_output": 65536}, lambda n, a: {})
        assert "error" in r
        assert "isolation" in r["error"].lower()
    finally:
        k.reset()


# ── the one-time "running UNCONFINED" heads-up (agent-side) ──────────────────
# When CodeAct is enabled but OS isolation is not in effect, the kernel runs
# model-authored Python with the same trust as Bash and raw stdlib bypasses the
# command + egress guards. The agent warns once (never blocks — opt-in stays).
from src.agent import codeact_unconfined_warning


def test_unconfined_warning_names_the_real_risk():
    msg = codeact_unconfined_warning("linux")
    assert "UNCONFINED" in msg
    assert "command" in msg and "egress" in msg  # the guards it bypasses


def test_unconfined_warning_points_at_the_opt_in_where_isolation_exists():
    # Linux (bubblewrap) AND macOS (Seatbelt) have a backend → point at the opt-in.
    for plat in ("linux", "darwin"):
        assert "KORGEX_CODEACT_ISOLATION=1" in codeact_unconfined_warning(plat), plat
    # a platform with no backend has nothing to point at
    other = codeact_unconfined_warning("win32")
    assert "unavailable on win32" in other
    assert "KORGEX_CODEACT_ISOLATION=1" not in other


# ── isolation MODE: required / off / auto (auto = the secure-by-default) ──────

def test_isolation_mode_parsing(monkeypatch):
    for v in ("1", "true", "yes", "on", "strict"):
        monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", v)
        assert S.isolation_mode() == "required", v
    for v in ("0", "false", "no", "off"):
        monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", v)
        assert S.isolation_mode() == "off", v
    monkeypatch.delenv("KORGEX_CODEACT_ISOLATION", raising=False)
    assert S.isolation_mode() == "auto"                 # UNSET → auto (the default)
    monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", "auto")
    assert S.isolation_mode() == "auto"


def test_would_run_unconfined_matrix(monkeypatch):
    # off → always unconfined
    monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", "off")
    assert S.would_run_unconfined() is True
    # required → never unconfined (sandboxes or fails closed)
    monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", "on")
    assert S.would_run_unconfined() is False
    # auto → unconfined ONLY when no backend is available
    monkeypatch.delenv("KORGEX_CODEACT_ISOLATION", raising=False)
    monkeypatch.setattr(S, "available", lambda: (True, "x"))
    assert S.would_run_unconfined() is False            # auto + backend → sandboxed
    monkeypatch.setattr(S, "available", lambda: (False, "none"))
    assert S.would_run_unconfined() is True             # auto + no backend → unconfined


def test_kernel_auto_runs_unconfined_without_a_backend(tmp_path, monkeypatch):
    # auto + no sandbox backend must NOT fail closed — CodeAct stays usable, just
    # unconfined (the agent warns). Only 'required' fails closed.
    monkeypatch.delenv("KORGEX_CODEACT_ISOLATION", raising=False)   # auto
    monkeypatch.setattr(S.sys, "platform", "win32")                 # no backend
    from src.codeact import KernelHandle
    k = KernelHandle(repo_root=str(tmp_path))
    try:
        r = k.exec("print(6 * 7)", {"wall_ms": 8000, "max_output": 65536}, lambda n, a: {})
        assert "error" not in r, r
        assert "42" in r.get("stdout", "")
        assert k._isolated is False                                 # ran unconfined
    finally:
        k.reset()


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"),
                    reason="auto-isolation end-to-end check needs darwin + sandbox-exec")
def test_kernel_auto_isolates_by_default_on_macos(tmp_path, monkeypatch):
    # The point of the follow-on: with isolation UNSET (auto), enabling CodeAct on a
    # box WITH a backend sandboxes by default — no env needed.
    monkeypatch.delenv("KORGEX_CODEACT_ISOLATION", raising=False)
    from src.codeact import KernelHandle
    k = KernelHandle(repo_root=str(tmp_path))
    try:
        r = k.exec("print('ok')", {"wall_ms": 8000, "max_output": 65536}, lambda n, a: {})
        assert "error" not in r and k._isolated is True            # auto → sandboxed
        net = k.exec("import socket\ntry:\n socket.create_connection(('1.1.1.1',80),3); print('NET')\n"
                     "except Exception as e: print('blocked')",
                     {"wall_ms": 8000, "max_output": 65536}, lambda n, a: {})
        assert "blocked" in net.get("stdout", "")                  # network denied by default
    finally:
        k.reset()
