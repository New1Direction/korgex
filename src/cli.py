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


def cmd_verify():
    """Prove the cognition ledger is intact (hash-chain + causal DAG)."""
    from src.korg_ledger import verify_journal_file, _ledger_hmac_key

    argv = sys.argv[1:]
    path = None
    if "verify" in argv:
        rest = [a for a in argv[argv.index("verify") + 1:] if not a.startswith("-")]
        if rest:
            path = rest[0]
    path = path or os.environ.get(
        "KORG_JOURNAL_PATH", str(Path(".korg") / "journal.jsonl"))

    if not Path(path).exists():
        print(f"  No ledger journal at {path}")
        print(f"  (set KORG_JOURNAL_PATH or pass: korgex verify <path>)")
        return 1

    n = sum(1 for ln in Path(path).read_text().splitlines() if ln.strip())
    errors = verify_journal_file(path, key=_ledger_hmac_key())
    if not errors:
        keyed = " (HMAC-keyed)" if _ledger_hmac_key() else ""
        print(f"  ✓ ledger intact — {n} events, hash-chain verified{keyed}")
        print(f"    {path}")
        return 0
    print(f"  ✗ ledger TAMPERED — {len(errors)} problem(s) in {path}:")
    for e in errors:
        print(f"      - {e}")
    return 1


def cmd_drift():
    """Scan persistent memories for drift against their recorded source baselines."""
    from src import memory as M
    from src import memory_drift as D

    M.init_memory(project_root=os.getcwd())
    memories = M.list_memories()
    if not memories:
        print("  No memories to scan.")
        return 0

    report = D.scan(memories, repo_root=os.getcwd())
    n = len(memories)
    fresh, drifted = len(report["fresh"]), len(report["drifted"])
    missing, unanchored = len(report["missing"]), len(report["unanchored"])

    if not report["has_drift"]:
        extra = f" ({unanchored} unanchored)" if unanchored else ""
        print(f"  ✓ {n} memories checked — no drift "
              f"({fresh} fresh){extra}")
        return 0

    print(f"  ✗ memory DRIFT — {drifted} drifted, {missing} missing "
          f"of {n} ({fresh} fresh, {unanchored} unanchored):")
    for v in report["verdicts"]:
        if v.get("status") in ("drifted", "missing"):
            print(f"      - {v['name']}: {v['status']} — {v['reason']}")
    print("    reconcile (keep / refresh / discard) is recorded to the ledger.")
    return 1


def cmd_import():
    """Replay another vendor's session transcript into a korg-ledger@v1 chained journal."""
    from src import import_adapters as IA

    argv = sys.argv[1:]
    rest, out = [], None
    if "import" in argv:
        toks = argv[argv.index("import") + 1:]
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in ("--out", "-o"):
                out = toks[i + 1] if i + 1 < len(toks) else None
                i += 2
                continue
            if not t.startswith("-"):
                rest.append(t)
            i += 1

    if len(rest) < 2:
        print("  usage: korgex import <vendor> <transcript> [--out journal.jsonl]")
        print(f"  vendors: {', '.join(sorted(IA.ADAPTERS))}")
        return 2

    vendor, transcript = rest[0], rest[1]
    out = out or (transcript.rsplit(".", 1)[0] + ".korg.jsonl")
    try:
        summary = IA.import_transcript(transcript, vendor=vendor, out_path=out)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"  import failed: {exc}")
        return 1

    status = "✓ verified intact" if summary["verified"] else f"✗ {summary['errors']}"
    print(f"  imported {summary['events']} events from '{vendor}' → {summary['out_path']}")
    print(f"  chain: {status}    ·    inspect: korgex verify {summary['out_path']}")
    return 0 if summary["verified"] else 1


def cmd_audit():
    """Audit an agent's session: import the logs you already have into a verifiable ledger."""
    from collections import Counter
    from src import import_adapters as IA

    argv = sys.argv[1:]
    root = session = out = None
    html_path = None
    if "audit" in argv:
        toks = argv[argv.index("audit") + 1:]
        i = 0
        while i < len(toks):
            t = toks[i]
            if t == "--root":
                root = toks[i + 1] if i + 1 < len(toks) else None
                i += 2
            elif t == "--session":
                session = toks[i + 1] if i + 1 < len(toks) else None
                i += 2
            elif t in ("--out", "-o"):
                out = toks[i + 1] if i + 1 < len(toks) else None
                i += 2
            elif t == "--html":
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt and not nxt.startswith("-"):
                    html_path = nxt
                    i += 2
                else:
                    html_path = ""  # sentinel: derive a path from the journal
                    i += 1
            else:
                i += 1

    if not session:
        found = IA.discover_claude_code_sessions(root=root)
        if not found:
            print("  No Claude Code sessions found under ~/.claude/projects.")
            print("  (or run: korgex audit --session <transcript.jsonl>)")
            return 1
        session = found[0]

    if not out:
        base = os.path.basename(session).rsplit(".", 1)[0]
        out = os.path.join(os.path.expanduser("~"), ".korgex", "audits", base + ".korg.jsonl")

    try:
        summary = IA.import_transcript(session, vendor="claude-code", out_path=out)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"  audit failed: {exc}")
        return 1

    events = []
    try:
        with open(out) as f:
            events = [json.loads(line) for line in f if line.strip()]
    except OSError:
        pass
    tools = Counter(e.get("tool_name") for e in events)
    top = ", ".join(f"{k}×{v}" for k, v in tools.most_common(6))

    if html_path is not None:
        from src import audit_report as AR

        if not html_path:
            stem = out[: -len(".korg.jsonl")] if out.endswith(".korg.jsonl") else out
            html_path = stem + ".html"
        try:
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(AR.render_html(events, {"session": os.path.basename(session), "vendor": "claude-code"}))
        except OSError as exc:
            print(f"  (html report failed: {exc})")
            html_path = None

    print(f"  audited {os.path.basename(session)} → {summary['events']} ledger events")
    if top:
        print(f"  activity: {top}")
    print(f"  journal:  {out}")
    if html_path:
        print(f"  report:   {html_path}  ← open in any browser; it re-verifies itself")
    if summary["verified"]:
        print("  chain:    ✓ INTACT — tamper-evident, cryptographically verifiable")
        print(f"  re-check any time:  korgex verify {out}")
        return 0
    print(f"  chain:    ✗ TAMPERED — {summary['errors'][:3]}")
    return 1


def cmd_mcp_server():
    """Run the korg-ledger MCP server (JSON-RPC over stdio) — verify/audit/import for any MCP host."""
    from src.mcp_server import serve
    serve()
    return 0


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
    "verify":            cmd_verify,
    "drift":             cmd_drift,
    "import":            cmd_import,
    "audit":             cmd_audit,
    "mcp-server":        cmd_mcp_server,
}


def run_agent_shim(prompt: str, model: str = None, resume: bool = False,
                   mode: str = None, mcp: bool = False, quiet: bool = False,
                   output_schema_path: str = None, effort: str = None) -> int:
    """Spawn the agent loop on a naked prompt. Returns a shell exit code."""
    output_schema = None
    if output_schema_path:
        try:
            import json as _json
            with open(output_schema_path) as f:
                output_schema = _json.load(f)
        except Exception as e:
            print(f"korgex: could not read --output-schema {output_schema_path}: {e}",
                  file=sys.stderr)
            return 2

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
        if effort:
            # korgantic max-power mode: effort-scaled workflow chain.
            kr = agent.run_korgantic_task(prompt, effort=effort)
            print(f"\nkorgantic[{kr['effort']}] — phases: {' → '.join(kr['phases_run'])}")
            if kr.get("findings"):
                print(f"  confirmed findings: {len(kr['findings'])}")
            missing = (kr.get("artifacts") or {}).get("completeness")
            if missing:
                print(f"  completeness gaps: {len(missing)}")
            return 0
        result = agent.run_task(prompt, output_schema=output_schema)
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
        sp = sub.add_parser(name, help=(fn.__doc__ or "").strip().split("\n")[0])
        if name == "verify":
            sp.add_argument("path", nargs="?",
                            help="Journal JSONL to verify "
                                 "(default: $KORG_JOURNAL_PATH or .korg/journal.jsonl)")
        elif name == "import":
            sp.add_argument("vendor", nargs="?", help="claude-code")
            sp.add_argument("transcript", nargs="?", help="path to the vendor session transcript")
            sp.add_argument("--out", "-o", help="output journal path (default: <transcript>.korg.jsonl)")
        elif name == "audit":
            sp.add_argument("--session", help="a specific transcript (default: newest Claude Code session)")
            sp.add_argument("--root", help="sessions root (default: ~/.claude/projects)")
            sp.add_argument("--out", "-o", help="output journal path")
            sp.add_argument("--html", nargs="?", const="",
                            help="also write a self-verifying HTML report (default: <journal>.html)")
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
    p.add_argument("--output-schema",
                   help="Path to a JSON Schema; the final answer is forced to "
                        "conform and is validated before returning (good for CI/piping).")
    p.add_argument("--effort",
                   choices=["auto", "low", "medium", "high", "xhigh", "ultracode"],
                   help="korgantic max-power mode: scale the effort. Chains "
                        "understand→design→implement→review with adversarial verify, "
                        "multi-modal sweep, completeness critic, loop-until-dry. "
                        "ultracode = token cost is not a constraint.")
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
        return SUBCOMMANDS[args.command]() or 0

    args = _build_prompt_parser().parse_args(argv)
    if not args.prompt_words:
        _build_subcommand_parser().print_help()
        return 0
    return run_agent_shim(" ".join(args.prompt_words),
                          model=args.model, resume=args.resume,
                          mode=args.mode, mcp=args.mcp, quiet=args.quiet,
                          output_schema_path=args.output_schema, effort=args.effort)


if __name__ == "__main__":
    sys.exit(main())