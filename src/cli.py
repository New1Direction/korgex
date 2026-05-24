"""
KorgKode CLI — the `korgkode` command. Works like `claude` from anywhere.

Usage:
    korgkode                  Start backend + open VS Code with sidecar
    korgkode init             One-shot setup: install deps, compile extension
    korgkode dashboard        Start the web dashboard only
    korgkode status           Check if backend is running
    korgkode stop             Stop the running backend
    korgkode install-extension Install VS Code extension from .vsix
"""

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
PID_FILE = Path(tempfile.gettempdir()) / "korgkode.pid"


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


def _start_background_server():
    """Launch the FastAPI dashboard in a subprocess."""
    if _is_running():
        _log(f"Backend already running (PID from {PID_FILE})")
        return

    dashboard = _resolve("src/dashboard.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, '{REPO_ROOT}'); "
         f"from src.dashboard import start_dashboard; "
         f"start_dashboard(port={DASHBOARD_PORT})"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    PID_FILE.write_text(str(proc.pid))
    _log(f"Backend started (PID {proc.pid}) on http://localhost:{DASHBOARD_PORT}")

    # Wait briefly to confirm it doesn't crash immediately
    time.sleep(1.5)
    if proc.poll() is not None:
        _log("Backend exited immediately — check dependencies (fastapi, uvicorn)")
        PID_FILE.unlink(missing_ok=True)
        sys.exit(1)


# ── Subcommands ──────────────────────────────────────────────────────────

def cmd_default():
    """Default: start backend + open VS Code with the sidecar."""
    _log("KorgKode — starting backend...")
    _start_background_server()

    code = _find_vscode()
    ext_path = _resolve("korgkode-vscode")

    _log(f"Opening VS Code at {ext_path}...")
    subprocess.Popen([code, str(ext_path)])

    print()
    print(f"  ┌─────────────────────────────────────────────┐")
    print(f"  │  KorgKode is live                           │")
    print(f"  │                                             │")
    print(f"  │  Dashboard  → http://localhost:{DASHBOARD_PORT:<4}           │")
    print(f"  │  VS Code    → Press F5 in the new window    │")
    print(f"  │  Commands   → Cmd+Shift+P → 'KorgKode:'     │")
    print(f"  │                                             │")
    print(f"  │  korgkode stop   to shut down               │")
    print(f"  └─────────────────────────────────────────────┘")


def cmd_init():
    """One-shot setup: install Python deps, compile the extension."""
    _log("KorgKode init — setting up...")

    # Python deps
    _log("Installing Python dependencies (fastapi, uvicorn)...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(REPO_ROOT)],
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "fastapi", "uvicorn"],
        capture_output=True,
    )

    # VS Code extension
    ext_path = _resolve("korgkode-vscode")
    _log("Installing Node dependencies...")
    subprocess.run(["npm", "install"], cwd=str(ext_path), capture_output=True)

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
    _log("Ready. Run `korgkode` to launch.")


def cmd_dashboard():
    """Start just the web dashboard."""
    _start_background_server()
    print(f"  Dashboard: http://localhost:{DASHBOARD_PORT}")
    print(f"  Press Ctrl+C to stop.")


def cmd_status():
    """Check if the backend is running."""
    if _is_running():
        pid = PID_FILE.read_text().strip()
        print(f"  KorgKode is running (PID {pid})")
        print(f"  Dashboard: http://localhost:{DASHBOARD_PORT}")
    else:
        print("  KorgKode is not running.")
        print(f"  Run `korgkode` to start.")


def cmd_stop():
    """Stop the running backend."""
    if not _is_running():
        print("  KorgKode is not running.")
        return

    pid = int(PID_FILE.read_text().strip())
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
        _log(f"KorgKode stopped (PID {pid})")
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        _log("KorgKode was already stopped.")


def cmd_install_extension():
    """Install the .vsix into VS Code."""
    vsix = _resolve("korgkode-vscode") / "korgkode-sidecar.vsix"
    if not vsix.exists():
        _log("No .vsix found. Run `korgkode init` first to compile.")
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

def main():
    cmds = {
        "init": cmd_init,
        "dashboard": cmd_dashboard,
        "status": cmd_status,
        "stop": cmd_stop,
        "install-extension": cmd_install_extension,
    }

    if len(sys.argv) > 1:
        cmd = cmds.get(sys.argv[1])
        if cmd:
            cmd()
            return
        print(f"Unknown subcommand: {sys.argv[1]}")
        print(f"Available: {' | '.join(['(default)'] + list(cmds.keys()))}")
        sys.exit(1)

    cmd_default()


if __name__ == "__main__":
    main()