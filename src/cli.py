"""
Korgex CLI — the `korgex` command. Works like `claude` from anywhere.

Usage:
    korgex                  Start backend + open VS Code with sidecar
    korgex init             One-shot setup: install deps, compile extension
    korgex dashboard        Start the web dashboard only
    korgex status           Check if backend is running
    korgex stop             Stop the running backend
    korgex install-extension Install VS Code extension from .vsix
"""

from __future__ import annotations

import os
import sys
import json
import time
import signal
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_PORT = 8090
PID_FILE = Path(tempfile.gettempdir()) / "korgex.pid"


# ── Helpers ──────────────────────────────────────────────────────────────

def _resolve(rel: str) -> Path:
    return REPO_ROOT / rel


def _log(msg: str):
    print(f"  ⚡ {msg}")


def _find_vscode() -> str:
    """Return the `code` CLI path — works for both stable and insiders."""
    for candidate in ["code", "code-insiders"]:
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return "code"  # fallback — let it fail with a clear message later


def _run_or_die(cmd: list[str], *, step: str, cwd: str | None = None) -> None:
    """Run a subprocess and exit with a clear error if it fails."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    {step} failed (exit {result.returncode}):")
        if result.stderr:
            print(result.stderr.rstrip())
        sys.exit(1)


def _is_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # signal 0 = existence check only
        return True
    except (ProcessLookupError, ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return False


_LAUNCHER_SRC = (
    "import os, sys; "
    "sys.path.insert(0, os.environ['KORGEX_REPO_ROOT']); "
    "from src.dashboard import start_dashboard; "
    "start_dashboard(port=int(os.environ['KORGEX_DASHBOARD_PORT']))"
)


def _start_background_server():
    """Launch the FastAPI dashboard in a subprocess."""
    if _is_running():
        _log(f"Backend already running (PID from {PID_FILE})")
        return

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["KORGEX_REPO_ROOT"] = str(REPO_ROOT)
    env["KORGEX_DASHBOARD_PORT"] = str(DASHBOARD_PORT)

    proc = subprocess.Popen(
        [sys.executable, "-c", _LAUNCHER_SRC],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    # Confirm the process actually stayed up before recording its PID. The
    # previous order wrote PID first and unlinked on death, leaving a
    # short race where `korgex status` could report "running" for a dead pid.
    time.sleep(1.5)
    if proc.poll() is not None:
        _log("Backend exited immediately — check dependencies (fastapi, uvicorn)")
        sys.exit(1)

    PID_FILE.write_text(str(proc.pid))
    _log(f"Backend started (PID {proc.pid}) on http://localhost:{DASHBOARD_PORT}")


# ── Subcommands ──────────────────────────────────────────────────────────

def cmd_default():
    """Default: start backend + open VS Code with the sidecar."""
    _log("Korgex — starting backend...")
    _start_background_server()

    code = _find_vscode()
    ext_path = _resolve("korgex-vscode")

    _log(f"Opening VS Code at {ext_path}...")
    subprocess.Popen([code, str(ext_path)])

    print()
    print(f"  ┌─────────────────────────────────────────────┐")
    print(f"  │  Korgex is live                           │")
    print(f"  │                                             │")
    print(f"  │  Dashboard  → http://localhost:{DASHBOARD_PORT:<4}           │")
    print(f"  │  VS Code    → Press F5 in the new window    │")
    print(f"  │  Commands   → Cmd+Shift+P → 'Korgex:'     │")
    print(f"  │                                             │")
    print(f"  │  korgex stop   to shut down               │")
    print(f"  └─────────────────────────────────────────────┘")


def cmd_init():
    """One-shot setup: install Python deps, compile the extension."""
    _log("Korgex init — setting up...")

    # Python deps
    _log("Installing Python dependencies (fastapi, uvicorn)...")
    _run_or_die(
        [sys.executable, "-m", "pip", "install", "-e", str(REPO_ROOT)],
        step="pip install -e .",
    )
    _run_or_die(
        [sys.executable, "-m", "pip", "install", "fastapi", "uvicorn"],
        step="pip install fastapi uvicorn",
    )

    # VS Code extension
    ext_path = _resolve("korgex-vscode")
    _log("Installing Node dependencies...")
    _run_or_die(["npm", "install"], step="npm install", cwd=str(ext_path))

    _log("Compiling TypeScript → JavaScript...")
    result = subprocess.run(
        ["npm", "run", "compile"],
        cwd=str(ext_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"    TypeScript compilation failed:\n{result.stderr}")
        sys.exit(1)

    print()
    _log("Ready. Run `korgex` to launch.")


def cmd_dashboard():
    """Start just the web dashboard."""
    _start_background_server()
    print(f"  Dashboard: http://localhost:{DASHBOARD_PORT}")
    print(f"  Press Ctrl+C to stop.")


def cmd_status():
    """Check if the backend is running."""
    if _is_running():
        pid = PID_FILE.read_text().strip()
        print(f"  Korgex is running (PID {pid})")
        print(f"  Dashboard: http://localhost:{DASHBOARD_PORT}")
    else:
        print("  Korgex is not running.")
        print(f"  Run `korgex` to start.")


def cmd_stop():
    """Stop the running backend."""
    if not _is_running():
        print("  Korgex is not running.")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError) as exc:
        PID_FILE.unlink(missing_ok=True)
        _log(f"PID file was corrupt ({exc}); cleared.")
        sys.exit(1)
    try:
        os.kill(pid, signal.SIGTERM)
        # Give it a moment, then force-kill
        time.sleep(1)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        PID_FILE.unlink(missing_ok=True)
        _log(f"Korgex stopped (PID {pid})")
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        _log("Korgex was already stopped.")


def cmd_install_extension():
    """Install the .vsix into VS Code."""
    vsix = _resolve("korgex-vscode") / "korgex-sidecar.vsix"
    if not vsix.exists():
        _log("No .vsix found. Run `korgex init` first to compile.")
        return

    code = _find_vscode()
    _log(f"Installing {vsix}...")
    result = subprocess.run(
        [code, "--install-extension", str(vsix)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        _log("Extension installed. Reload VS Code to activate.")
    else:
        print(f"    Install failed:\n{result.stderr}")


# ── Entry Point ──────────────────────────────────────────────────────────

import argparse

# Map subcommand name → handler. Existing bodies untouched.
SUBCOMMANDS = {
    "serve":             cmd_default,             # default behavior: dashboard + VS Code
    "dashboard":         cmd_dashboard,           # dashboard only
    "init":              cmd_init,
    "status":            cmd_status,
    "stop":              cmd_stop,
    "install-extension": cmd_install_extension,
}


def run_agent_shim(prompt: str, model: str = None, resume: bool = False,
                   mode: str = None, mcp: bool = False, quiet: bool = False) -> int:
    """Spawn the agent loop on a naked prompt. Returns a shell exit code."""
    try:
        from src.agent import KorgexAgent
    except Exception as e:
        print(f"korgex: failed to import agent: {e}", file=sys.stderr)
        return 2

    # interactive=None lets the agent auto-detect TTY; quiet forces off
    interactive = False if quiet else None

    if resume:
        print(
            "korgex: --resume is not yet implemented. "
            "Exiting to avoid silently ignoring your intent in scripts/CI. "
            "(Track: https://github.com/New1Direction/Korgex/issues)",
            file=sys.stderr,
        )
        return 2

    try:
        agent = KorgexAgent(model=model, mode=mode,
                              interactive=interactive, load_mcp=mcp)
        result = agent.run_task(prompt)
    except RuntimeError as e:
        print(f"korgex: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"korgex: agent crashed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    text = (result or {}).get("result", "")
    if text and quiet:
        # In quiet mode the streamer didn't print; emit the final text now
        print(text)
    return 0 if (result or {}).get("success", False) else 1


_DESCRIPTION = ("Korgex — autonomous coding agent. "
                "Pass a naked prompt to run the agent, or use a subcommand.")

_EPILOG = ("Examples:\n"
           "  korgex \"fix the auth bug\"     # run the agent on a task\n"
           "  korgex serve                    # start dashboard + open VS Code\n"
           "  korgex dashboard                # start dashboard only\n"
           "  korgex init                     # install deps + compile extension\n"
           "  korgex status                   # show backend status\n"
           "  korgex stop                     # stop background backend\n")


def _build_subcommand_parser():
    p = argparse.ArgumentParser(prog="korgex", description=_DESCRIPTION, epilog=_EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", metavar="SUBCOMMAND")
    for name, fn in SUBCOMMANDS.items():
        sub.add_parser(name, help=(fn.__doc__ or "").strip().split("\n")[0])
    return p


def _build_prompt_parser():
    p = argparse.ArgumentParser(prog="korgex", description=_DESCRIPTION, epilog=_EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", help="Override model (e.g. claude-sonnet-4-6, gpt-4o)")
    p.add_argument("--mode",
                   choices=["plan", "execute", "explore", "review", "debug", "research"],
                   help="Mode-based model selection (e.g. plan → Opus, execute → Sonnet)")
    p.add_argument("--mcp", action="store_true",
                   help="Load MCP servers from mcp.json at startup")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="Disable streaming TUI; print only the final result")
    p.add_argument("--resume", action="store_true", help="Resume the last session")
    p.add_argument("prompt_words", nargs="*", help="Task description for the agent")
    return p


def _get_version() -> str:
    """Best-effort version lookup. Falls back to '0.0.0+dev' if package
    metadata isn't available (e.g. running from a checkout without install)."""
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("korgex")
    except Exception:
        return "0.0.0+dev"


def main():
    argv = sys.argv[1:]

    # --introspect short-circuit. Foundry-style pre-parse: scan raw argv
    # before any parser builds or imports run, so the JSON document on
    # stdout is never polluted by import-time prints or argparse errors
    # from missing positional args.
    if "--introspect" in argv:
        from src.introspect import emit as _emit_introspect
        _emit_introspect(_get_version())
        return 0

    # Decide which parser to use up-front:
    #   - any token equal to a known subcommand → subcommand parser
    #   - just --help / -h → subcommand parser (it has the richer help)
    #   - otherwise → prompt parser
    is_subcommand = any(tok in SUBCOMMANDS for tok in argv)
    is_help_only = argv in ([], ["-h"], ["--help"])

    if is_subcommand or is_help_only:
        args = _build_subcommand_parser().parse_args(argv)
        if not args.command:
            _build_subcommand_parser().print_help()
            return 0
        SUBCOMMANDS[args.command]()
        return 0

    args = _build_prompt_parser().parse_args(argv)
    if not args.prompt_words:
        _build_subcommand_parser().print_help()
        return 0
    return run_agent_shim(" ".join(args.prompt_words),
                          model=args.model, resume=args.resume,
                          mode=args.mode, mcp=args.mcp, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())