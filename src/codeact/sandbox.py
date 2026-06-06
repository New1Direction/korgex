"""OS-level isolation for the CodeAct kernel (Linux: bubblewrap · macOS: Seatbelt).

CodeAct runs model-authored Python in a subprocess that is SAME-TRUST as Bash: raw
stdlib (``open``/``os``/``socket``/``subprocess``) bypasses the governed,
ledger-recorded tool bridge. This OPT-IN sandbox wraps that subprocess so the kernel
can only WRITE inside the workspace and has NO network — forcing file-mutation and
network egress through the bridge, which runs in the PARENT (outside the sandbox) and
is governed + recorded. Reads of the wider filesystem still work, but with no network
they can't be exfiltrated.

Two backends, the SAME two guarantees (no network · write only the workspace):
  - Linux → ``bwrap`` (bubblewrap): whole fs read-only, private ``/tmp``, ``--unshare-net``.
  - macOS → ``sandbox-exec`` (Seatbelt/SBPL): ``(allow default)`` then ``(deny network*)``
    and ``(deny file-write*)`` everywhere except the workspace + the per-user temp dir.

Design choices:
  - OFF by default (``KORGEX_CODEACT_ISOLATION``). It changes execution semantics, so
    it's strictly opt-in.
  - FAIL-CLOSED: when isolation is REQUESTED but the platform's sandbox tool isn't
    present, the kernel refuses to start rather than run unconfined (the caller
    surfaces a clear error). Never a silent downgrade.
  - DECLARATIVE + auditable by reading (the bwrap flag set / the SBPL profile), and
    live-validated on each OS. A zero-external-dep Landlock/seccomp backend is a
    future option.

Pure helpers (the sandbox-tool path is injectable) so the wiring is testable on any OS.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

_ON = ("1", "true", "yes", "on", "strict")


def isolation_requested() -> bool:
    """True iff the user opted into sandboxed CodeAct (KORGEX_CODEACT_ISOLATION)."""
    return os.environ.get("KORGEX_CODEACT_ISOLATION", "off").strip().lower() in _ON


def available() -> tuple[bool, str]:
    """``(ok, reason)`` — whether this platform's CodeAct sandbox tool is present.

    Linux needs bubblewrap (``bwrap``); macOS needs ``sandbox-exec`` (Seatbelt). The
    reason explains the gap when ok is False, so the caller can fail closed with a
    message the model/user can act on."""
    if sys.platform == "darwin":
        if shutil.which("sandbox-exec"):
            return True, "sandbox-exec"
        return False, "CodeAct isolation needs sandbox-exec (not found on PATH)"
    if sys.platform.startswith("linux"):
        if shutil.which("bwrap"):
            return True, "bwrap"
        return False, "CodeAct isolation needs bubblewrap (bwrap) on PATH"
    return False, f"CodeAct isolation has no backend for {sys.platform}"


def wrap_command(argv: list, workspace_root: str, install_root: str | None = None,
                 *, bwrap: str | None = None, sandbox_exec: str | None = None) -> list:
    """Prefix ``argv`` with this platform's OS sandbox — bubblewrap on Linux,
    Seatbelt (``sandbox-exec``) on macOS. Both enforce the same two guarantees: NO
    network and WRITE only inside the workspace (reads of the wider fs still work).
    stdio is passed through untouched, so the kernel↔parent protocol still reaches the
    governed bridge in the unsandboxed parent. ``install_root`` is only needed by the
    bubblewrap backend (Seatbelt allows reads by default)."""
    if sys.platform == "darwin":
        return _seatbelt_command(argv, workspace_root, sandbox_exec=sandbox_exec)
    return _bwrap_command(argv, workspace_root, install_root, bwrap=bwrap)


def _bwrap_command(argv: list, workspace_root: str, install_root: str | None = None,
                   *, bwrap: str | None = None) -> list:
    """Linux bubblewrap wrapper.

    Layout: the whole filesystem is bound READ-ONLY (so python + stdlib + libs load),
    with minimal ``/dev`` + ``/proc`` and a PRIVATE writable ``/tmp``; the workspace
    is the ONLY writable real path; network is removed (``--unshare-net``); the
    sandbox dies with the parent.

    ``install_root`` (where the korgex package lives, on the child's PYTHONPATH) is
    re-bound read-only AFTER the ``/tmp`` tmpfs, so the kernel can still import the
    package even when korgex is installed/checked-out UNDER ``/tmp`` (e.g. a CI
    clone) — otherwise the tmpfs would hide it and the kernel couldn't start."""
    b = bwrap or shutil.which("bwrap") or "bwrap"
    ws = os.path.abspath(workspace_root)
    out = [
        b,
        "--ro-bind", "/", "/",      # whole fs READABLE (python, stdlib, shared libs)
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",          # private writable scratch (real /tmp hidden)
    ]
    if install_root:
        ir = os.path.abspath(install_root)
        out += ["--ro-bind", ir, ir]  # re-expose the package even if it's under /tmp
    out += [
        "--bind", ws, ws,           # the workspace is the ONLY writable real path
        "--chdir", ws,
        "--unshare-net",            # NO network egress
        "--unshare-pid",
        "--die-with-parent",
        "--", *argv,
    ]
    return out


def _sbpl_str(path: str) -> str:
    """Escape a path for an SBPL double-quoted string literal (backslash, then quote)."""
    return path.replace("\\", "\\\\").replace('"', '\\"')


def _seatbelt_profile(workspace_root: str, tmpdir: str | None = None) -> str:
    """A Seatbelt (SBPL) profile mirroring the bwrap guarantees: ``(allow default)``
    then DENY all network and DENY every file-write outside the workspace + the
    per-user temp dir (+ the stdio character devices).

    SBPL is last-match-wins, so the broad ``(deny file-write*)`` is re-opened only for
    the allowed subpaths. Paths are realpath-canonicalized because macOS ``/tmp``,
    ``/var``, ``/etc`` are symlinks into ``/private`` and Seatbelt matches the
    RESOLVED path — a workspace under ``/tmp`` would otherwise never match."""
    ws = _sbpl_str(os.path.realpath(workspace_root))
    tmp = _sbpl_str(os.path.realpath(tmpdir or tempfile.gettempdir()))
    return "\n".join([
        "(version 1)",
        "(allow default)",
        "(deny network*)",            # NO network egress (mirrors --unshare-net)
        "(deny file-write*)",         # then re-open writes only where allowed:
        "(allow file-write*",
        f'    (subpath "{ws}")',      # the workspace — the only writable real tree
        f'    (subpath "{tmp}")',     # the per-user temp dir (ephemeral scratch)
        '    (literal "/dev/null")',
        '    (literal "/dev/zero")',
        '    (literal "/dev/stdout")',
        '    (literal "/dev/stderr")',
        '    (regex #"^/dev/tty")',
        '    (regex #"^/dev/fd/"))',
    ])


def _seatbelt_command(argv: list, workspace_root: str, *,
                      sandbox_exec: str | None = None, tmpdir: str | None = None) -> list:
    """macOS Seatbelt wrapper: ``sandbox-exec -p <profile> argv``. The profile is
    inlined (no temp file). cwd + env are inherited from the parent Popen, and stdio
    passes through untouched, so the governed bridge still reaches the parent."""
    se = sandbox_exec or shutil.which("sandbox-exec") or "sandbox-exec"
    return [se, "-p", _seatbelt_profile(workspace_root, tmpdir=tmpdir), *argv]
