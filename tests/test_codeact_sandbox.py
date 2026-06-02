"""CodeAct OS-sandbox (bubblewrap) — the pure wiring (Linux-only confinement).

CodeAct's kernel is SAME-TRUST as Bash: raw stdlib (open/os/socket/subprocess)
bypasses the governed, ledger-recorded bridge. This OPT-IN sandbox wraps the kernel
subprocess in bubblewrap so it can only WRITE inside the workspace and has NO network
— forcing file-mutation + egress through the bridge (which runs in the unsandboxed
parent). OFF by default; FAIL-CLOSED when isolation is requested but unavailable
(never silently runs unconfined).

These pin the pure logic — runnable on ANY OS (the bwrap path is injected, platform
is monkeypatched). The actual confinement needs a Linux box with bwrap; it's verified
here by construction (the declarative bwrap flag set).
"""
from __future__ import annotations

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


def test_unavailable_on_non_linux(monkeypatch):
    monkeypatch.setattr(S.sys, "platform", "darwin")
    ok, why = S.available()
    assert ok is False and "linux" in why.lower()


def test_unavailable_when_bwrap_missing(monkeypatch):
    monkeypatch.setattr(S.sys, "platform", "linux")
    monkeypatch.setattr(S.shutil, "which", lambda n: None)
    ok, why = S.available()
    assert ok is False and "bwrap" in why.lower()


def test_available_on_linux_with_bwrap(monkeypatch):
    monkeypatch.setattr(S.sys, "platform", "linux")
    monkeypatch.setattr(S.shutil, "which", lambda n: "/usr/bin/bwrap")
    ok, _ = S.available()
    assert ok is True


def test_wrap_command_confines_workspace_and_kills_network(tmp_path):
    argv = ["/usr/bin/python3", "-u", "-m", "src.codeact.kernel_main"]
    out = S.wrap_command(argv, str(tmp_path), bwrap="/usr/bin/bwrap")
    assert out[0] == "/usr/bin/bwrap"
    assert out[-len(argv):] == argv          # the real command runs after `--`, untouched
    assert "--unshare-net" in out            # NO network egress
    assert "--ro-bind" in out                # rest of fs read-only
    assert "--die-with-parent" in out
    # workspace is bound read-WRITE as a `--bind <ws> <ws>` pair
    ws = str(tmp_path)
    i = out.index("--bind")
    assert out[i + 1] == ws and out[i + 2] == ws


def test_wrap_command_rebinds_install_root_after_tmpfs(tmp_path):
    # REGRESSION (Linux dogfood): --tmpfs /tmp hides a korgex install located under
    # /tmp (e.g. a CI clone), so the kernel couldn't import the package inside the
    # sandbox. The install_root must be re-bound read-only AFTER the tmpfs.
    install = tmp_path / "install"
    ws = tmp_path / "ws"
    out = S.wrap_command(["python3"], str(ws), str(install), bwrap="/usr/bin/bwrap")
    # the install_root re-bind appears, and AFTER the /tmp tmpfs (so it wins if under /tmp)
    ir = str(install)
    assert ir in out
    tmpfs_i = out.index("--tmpfs")
    ir_i = out.index(ir)
    assert ir_i > tmpfs_i                  # re-bound AFTER the /tmp tmpfs
    assert out[ir_i - 1] == "--ro-bind"    # as a `--ro-bind <ir> <ir>` pair
    assert out[ir_i + 1] == ir


def test_kernel_spawn_fails_closed_when_isolation_unavailable(tmp_path, monkeypatch):
    # Requesting isolation on a box without it (e.g. macOS) must FAIL CLOSED: the
    # kernel refuses to start rather than run model code unconfined.
    monkeypatch.setenv("KORGEX_CODEACT_ISOLATION", "on")
    monkeypatch.setattr(S.sys, "platform", "darwin")  # force unavailable, deterministically
    from src.codeact import KernelHandle
    k = KernelHandle(repo_root=str(tmp_path))
    try:
        r = k.exec("1 + 1", {"wall_ms": 4000, "max_output": 65536}, lambda n, a: {})
        assert "error" in r
        assert "isolation" in r["error"].lower()
    finally:
        k.reset()
