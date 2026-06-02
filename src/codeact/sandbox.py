"""OS-level isolation for the CodeAct kernel (Linux + bubblewrap).

CodeAct runs model-authored Python in a subprocess that is SAME-TRUST as Bash: raw
stdlib (``open``/``os``/``socket``/``subprocess``) bypasses the governed,
ledger-recorded tool bridge. This OPT-IN sandbox wraps that subprocess in
bubblewrap so the kernel can only WRITE inside the workspace and has NO network —
forcing file-mutation and network egress through the bridge, which runs in the
PARENT (outside the sandbox) and is governed + recorded. Reads of the wider
filesystem still work, but with no network they can't be exfiltrated.

Design choices:
  - OFF by default (``KORGEX_CODEACT_ISOLATION``). It changes execution semantics, so
    it's strictly opt-in.
  - FAIL-CLOSED: when isolation is REQUESTED but Linux+bwrap aren't both present, the
    kernel refuses to start rather than run unconfined (the caller surfaces a clear
    error). Never a silent downgrade.
  - bubblewrap, not ctypes-Landlock: the flag set is DECLARATIVE and auditable by
    reading — which matters because the confinement can only be live-validated on a
    Linux box. A zero-external-dep Landlock/seccomp backend is a future option.

Pure helpers (the bwrap path is injectable) so the wiring is testable on any OS.
"""
from __future__ import annotations

import os
import shutil
import sys

_ON = ("1", "true", "yes", "on", "strict")


def isolation_requested() -> bool:
    """True iff the user opted into sandboxed CodeAct (KORGEX_CODEACT_ISOLATION)."""
    return os.environ.get("KORGEX_CODEACT_ISOLATION", "off").strip().lower() in _ON


def available() -> tuple[bool, str]:
    """``(ok, reason)`` — ok only on Linux with bubblewrap (``bwrap``) on PATH.

    The reason explains the gap when ok is False, so the caller can fail closed with
    a message the model/user can act on."""
    if not sys.platform.startswith("linux"):
        return False, f"CodeAct isolation needs Linux (this is {sys.platform})"
    if not shutil.which("bwrap"):
        return False, "CodeAct isolation needs bubblewrap (bwrap) on PATH"
    return True, "bwrap"


def wrap_command(argv: list, workspace_root: str, install_root: str | None = None,
                 *, bwrap: str | None = None) -> list:
    """Prefix ``argv`` with a bubblewrap sandbox.

    Layout: the whole filesystem is bound READ-ONLY (so python + stdlib + libs load),
    with minimal ``/dev`` + ``/proc`` and a PRIVATE writable ``/tmp``; the workspace
    is the ONLY writable real path; network is removed (``--unshare-net``); the
    sandbox dies with the parent. The kernel↔parent protocol rides stdio, which bwrap
    passes through untouched, so the governed bridge still reaches the (unsandboxed)
    parent.

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
