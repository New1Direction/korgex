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

def cmd_skills():
    """Print all available skills, or `korgex skills log` for the agent's verifiable
    self-improvement audit trail (what it learned/curated/aged + why)."""
    argv = sys.argv[1:]
    rest = ([a for a in argv[argv.index("skills") + 1:] if not a.startswith("-")]
            if "skills" in argv else [])
    if rest and rest[0].lower() == "log":
        return _cmd_skills_log(rest[1] if len(rest) > 1 else None)

    from src.skills import load_skills, default_skill_roots
    # Pass cwd so project-local .korgex/skills are listed too, not just
    # built-in + user-global.
    skill_registry = load_skills(default_skill_roots(os.getcwd()))
    for name in skill_registry.names():
        skill = skill_registry.get(name)
        print(f"{skill.name}: {skill.description}")
    return 0


def _cmd_skills_log(path=None):
    """`korgex skills log` — read skill self-improvement events back from the ledger."""
    from src import skill_ledger as SL
    from src.korg_ledger import load_journal_raw
    path = path or os.environ.get("KORG_JOURNAL_PATH", str(Path(".korg") / "journal.jsonl"))
    if not os.path.exists(path):
        print(f"  No ledger journal at {path}")
        print("  (the agent records what it learns/curates here as it works)")
        return 1
    try:
        rows = SL.skill_log(load_journal_raw(path))
    except Exception:
        rows = []
    if not rows:
        print("  no skill self-modifications recorded yet")
        return 0
    print("  skill self-improvement log (from the verifiable ledger):")
    for row in rows:
        print("    " + SL.format_row(row))
    print(f"  {len(rows)} skill event(s) · korgex why <skill> traces one back to its prompt")
    return 0

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
    """Scaffold an AGENTS.md for this project (the guide korgex reads each session)."""
    import os

    from src import project_init as PI

    root = os.getcwd()
    res = PI.scaffold(root)
    if not res["written"]:
        _log(f"AGENTS.md already exists at {res['path']} — leaving it untouched.")
        return
    facts = res.get("facts", {})
    langs = ", ".join(facts.get("languages") or []) or "none detected"
    _log(f"Created {res['path']}")
    line = f"Detected: {langs}"
    if facts.get("test_cmd"):
        line += f"  ·  test: {facts['test_cmd']}"
    _log(line)
    _log("Fill in the TODO sections (overview, conventions). korgex reads this — "
         "and any nested AGENTS.md / .korgex/rules — automatically each session.")


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
    from src.korg_ledger import verify_journal_file, load_journal_raw, _ledger_hmac_key

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

    n = len(load_journal_raw(path))  # real event count (array OR jsonl), not line count
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


def cmd_trace():
    """Show the causal cognition trace (what the agent did + what caused it)."""
    from src import recall as R
    from src.ledger_trace import render_trace

    argv = sys.argv[1:]
    path = None
    if "trace" in argv:
        rest = [a for a in argv[argv.index("trace") + 1:] if not a.startswith("-")]
        if rest:
            path = rest[0]
    path = path or os.environ.get(
        "KORG_JOURNAL_PATH", str(Path(".korg") / "journal.jsonl"))

    if not Path(path).exists():
        print(f"  No ledger journal at {path}")
        print("  (set KORG_JOURNAL_PATH or pass: korgex trace <path>)")
        return 1

    events = R.load_events(path)
    out = render_trace(events, color=sys.stdout.isatty())
    if not out:
        print("  (no cognition recorded yet)")
        return 0
    print(out)
    from src.cost import estimate_cost, format_cost
    print(f"\n  {format_cost(estimate_cost(events))}")
    print(f"  {len(events)} events · prove it wasn't edited:  korgex verify {path}")
    return 0


def cmd_cost():
    """Estimated $ spend for the session, from the ledger's recorded token counts."""
    from src import recall as R
    from src.cost import estimate_cost, format_cost

    argv = sys.argv[1:]
    rest = ([a for a in argv[argv.index("cost") + 1:] if not a.startswith("-")]
            if "cost" in argv else [])
    path = rest[0] if rest else os.environ.get(
        "KORG_JOURNAL_PATH", str(Path(".korg") / "journal.jsonl"))
    if not Path(path).exists():
        print(f"  No ledger journal at {path}")
        return 1
    s = estimate_cost(R.load_events(path))
    print(f"  {format_cost(s)}")
    for model, m in sorted(s["by_model"].items(), key=lambda kv: -kv[1]["usd"]):
        mark = "" if m["known"] else "  (unpriced)"
        print(f"    {model:<28} {m['input']:>9,} in  {m['output']:>9,} out  ${m['usd']:.4f}{mark}")
    print("  tokens are from the verifiable ledger; $ is an estimate (public list prices).")
    return 0


def cmd_why():
    """Trace why a file was changed — back through the causal chain to its prompt."""
    from src import recall as R
    from src.ledger_trace import explain_why

    argv = sys.argv[1:]
    rest = ([a for a in argv[argv.index("why") + 1:] if not a.startswith("-")]
            if "why" in argv else [])
    if not rest:
        print("  usage: korgex why <file> [journal]")
        return 2
    target = rest[0]
    path = rest[1] if len(rest) > 1 else os.environ.get(
        "KORG_JOURNAL_PATH", str(Path(".korg") / "journal.jsonl"))
    if not Path(path).exists():
        print(f"  No ledger journal at {path}")
        return 1
    print(explain_why(R.load_events(path), target, color=sys.stdout.isatty()))
    return 0


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


def cmd_trajectory():
    """Export a korg-ledger journal as a verifiable, provenance-stamped training trajectory."""
    from src import trajectory as TJ

    argv = sys.argv[1:]
    journal = out = None
    if "trajectory" in argv:
        toks = argv[argv.index("trajectory") + 1:]
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in ("--out", "-o"):
                out = toks[i + 1] if i + 1 < len(toks) else None
                i += 2
            elif not t.startswith("-") and journal is None:
                journal = t
                i += 1
            else:
                i += 1

    journal = journal or os.environ.get("KORG_JOURNAL_PATH") or os.path.join(".korg", "journal.jsonl")
    if not os.path.exists(journal):
        print(f"  No journal at {journal}")
        print("  usage: korgex trajectory <journal.jsonl> [-o trajectories.jsonl]")
        return 1
    try:
        s = TJ.export_trajectory(journal, out)
    except (OSError, ValueError) as exc:
        print(f"  trajectory export failed: {exc}")
        return 1

    print(f"  trajectory: {s['turns']} turns from {s['events']} events → {s['out_path']}")
    if s["verified"]:
        print("  provenance: ✓ VERIFIED — derived from an intact, tamper-evident chain")
        return 0
    print("  provenance: ✗ UNVERIFIED — the source chain does not verify (possible tampering)")
    return 1


def cmd_diag():
    """Report language-server diagnostics (errors/types) for a file — best-effort."""
    import shutil

    from src import lsp

    argv = sys.argv[1:]
    path = None
    if "diag" in argv:
        for t in argv[argv.index("diag") + 1:]:
            if not t.startswith("-"):
                path = t
                break
    if not path:
        print("  usage: korgex diag <file>")
        return 1
    if not os.path.exists(path):
        print(f"  no such file: {path}")
        return 1

    srv = lsp.server_for(path)
    if not srv:
        print(f"  no language server configured for {os.path.splitext(path)[1] or '(no ext)'}")
        return 0
    if not shutil.which(srv[0]):
        print(f"  language server not installed: {srv[0]}  (install it to get diagnostics)")
        return 0

    diags = lsp.diagnostics(path)
    if not diags:
        print(f"  ✓ no diagnostics — {os.path.basename(path)} is clean")
        return 0
    sev = {1: "error", 2: "warn", 3: "info", 4: "hint"}
    for d in diags:
        line = ((d.get("range") or {}).get("start") or {}).get("line", 0) + 1
        print(f"  {sev.get(d.get('severity'), '?'):5} {path}:{line}  {d.get('message', '')}")
    return 1 if any(d.get("severity") == 1 for d in diags) else 0


def cmd_bus():
    """Verifiable agent message bus — agents coordinate over a tamper-evident korg-ledger journal."""
    from src import bus as B

    argv = sys.argv[1:]
    toks = argv[argv.index("bus") + 1:] if "bus" in argv else []
    journal = os.environ.get("KORG_BUS_JOURNAL") or os.path.join(
        os.path.expanduser("~"), ".korg", "bus.jsonl")
    if not toks:
        print("  usage: korgex bus <send|inbox|history|members> …")
        print("    korgex bus send <from> <to> <message>")
        print("    korgex bus inbox <agent>")
        return 1

    action, rest = toks[0], toks[1:]
    if action == "send":
        if len(rest) < 3:
            print("  usage: korgex bus send <from> <to> <message>")
            return 1
        seq = B.send(journal, rest[0], rest[1], " ".join(rest[2:]))
        print(f"  ✓ #{seq}  {rest[0]} → {rest[1]}  (chained + verifiable)")
        return 0
    if action == "inbox":
        if not rest:
            print("  usage: korgex bus inbox <agent>")
            return 1
        msgs = B.inbox(journal, rest[0])
        if not msgs:
            print(f"  no unread for {rest[0]}")
            return 0
        for m in msgs:
            print(f"  #{m['seq']}  {m['from']} → {m['to']}:  {m['body']}")
        B.mark_read(journal, rest[0], [m["seq"] for m in msgs])
        return 0
    if action == "history":
        for m in B.history(journal):
            print(f"  #{m['seq']}  {m['from']} → {m['to']}:  {m['body']}")
        return 0
    if action == "members":
        mem = B.members(journal)
        print("  " + (", ".join(mem) if mem else "(none yet)"))
        return 0
    print(f"  unknown bus action: {action}")
    return 1


def cmd_mcp_server():
    """Run the korg-ledger MCP server (JSON-RPC over stdio) — verify/audit/import for any MCP host."""
    from src.mcp_server import serve
    serve()
    return 0


def cmd_mcp():
    """Manage MCP servers — add/list/remove stdio or remote (url+auth) servers in mcp.json."""
    from src import mcp_admin

    argv = sys.argv[1:]
    toks = argv[argv.index("mcp") + 1:] if "mcp" in argv else []
    if not toks or toks[0] in ("-h", "--help"):
        print("  usage: korgex mcp <list|catalog|add|remove|login> …")
        print("    korgex mcp catalog")
        print("    korgex mcp list")
        print("    korgex mcp add <name|alias> [--global] [--command … --args … | --url … --header …]")
        print("    korgex mcp remove <name> [--global]")
        print("    korgex mcp login <name>   # OAuth a remote server in the browser")
        return 0 if toks else 1

    import os
    global_path = os.path.join(os.path.expanduser("~"), ".korgex", "mcp.json")

    action, rest = toks[0], toks[1:]
    if action == "catalog":
        from src import mcp_catalog
        print("  MCP catalog — add with `korgex mcp add <alias> [--global]`:")
        for e in mcp_catalog.entries():
            needs = f"  (needs {', '.join(e['needs'])})" if e["needs"] else ""
            print(f"  {e['alias']:<20} [{e['transport']}]  {e['description']}{needs}")
        return 0
    if action == "list":
        rows = mcp_admin.mcp_list()
        if not rows:
            print("  no MCP servers configured — `korgex mcp catalog` then `korgex mcp add <alias>`")
            return 0
        for r in rows:
            print(f"  {r['name']:<22} [{r['transport']}]  {r['target']}")
        return 0
    if action == "login":
        names = [t for t in rest if not t.startswith("-")]
        if not names:
            print("  usage: korgex mcp login <name>")
            return 1
        name = names[0]
        # find the server's url (configured first, then catalog)
        from src import mcp_catalog, mcp_config, mcp_oauth
        servers = mcp_config.load_servers(cwd=os.getcwd())
        url = servers[name].url if name in servers and servers[name].url else None
        if not url:
            preset = mcp_catalog.resolve(name)
            url = (preset or {}).get("url")
        if not url:
            print(f"  '{name}' isn't a known remote (http) server. Add it first: "
                  f"korgex mcp add {name} --url <url> [--global]")
            return 1
        res = mcp_oauth.login(name, url)
        if res.get("ok"):
            print(f"  ✓ logged in to {name} — token stored; it'll be applied automatically")
            return 0
        print(f"  login failed: {res.get('error')}")
        return 1
    if action == "remove":
        names = [t for t in rest if not t.startswith("-")]
        if not names:
            print("  usage: korgex mcp remove <name> [--global]")
            return 1
        path = global_path if "--global" in rest else "mcp.json"
        ok = mcp_admin.mcp_remove(names[0], path=path)
        print(f"  {'✓ removed' if ok else '· not found'}: {names[0]}")
        return 0 if ok else 1
    if action == "add":
        ap = argparse.ArgumentParser(prog="korgex mcp add", add_help=False)
        ap.add_argument("name")
        ap.add_argument("--command")
        ap.add_argument("--args")
        ap.add_argument("--url")
        ap.add_argument("--env", action="append", default=[])
        ap.add_argument("--header", action="append", default=[])
        ap.add_argument("--path", help="directory for the filesystem preset")
        ap.add_argument("--global", dest="is_global", action="store_true",
                        help="write to ~/.korgex/mcp.json (available in any directory)")
        try:
            a = ap.parse_args(rest)
        except SystemExit:
            return 1
        path = global_path if a.is_global else "mcp.json"
        where = "global (~/.korgex/mcp.json)" if a.is_global else "project (mcp.json)"

        # Bare `add <alias>` with no --command/--url → resolve from the catalog.
        if not a.url and not a.command:
            from src import mcp_catalog
            preset = mcp_catalog.resolve(a.name, path_value=a.path or os.getcwd())
            if preset is None:
                print(f"  '{a.name}' isn't a catalog preset, and no --command/--url given.")
                print("  → see `korgex mcp catalog`, or pass --command <cmd> / --url <url>")
                return 1
            mcp_admin.mcp_add(a.name, path=path, **preset)
            tgt = preset.get("url") or (preset.get("command", "") + " " + " ".join(preset.get("args", []))).strip()
            print(f"  ✓ added {a.name} (from catalog) → {tgt}  [{where}]")
            print("  korgex auto-connects it at startup")
            return 0

        env = dict(kv.split("=", 1) for kv in a.env if "=" in kv)
        headers = {}
        for h in a.header:
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
        args_list = a.args.split() if a.args else None
        mcp_admin.mcp_add(a.name, command=a.command, args=args_list, env=env or None,
                          url=a.url, headers=headers or None, path=path)
        print(f"  ✓ added {a.name} → {a.url or (a.command + (' ' + a.args if a.args else ''))}  [{where}]")
        print("  korgex now auto-connects configured servers at startup")
        return 0
    print(f"  unknown action: {action} — use list|add|remove")
    return 1


def cmd_setup():
    """Connect model providers (any of them) — saves keys + a default model to ~/.korgex/config.json."""
    from src.setup_wizard import run_setup
    return run_setup()


def _record_local(name: str, payload: dict) -> None:
    """Best-effort verifiable record of a local-model decision (never fatal)."""
    try:
        from src import korg_ledger as KL
        KL.get_default_client().record_tool_call(
            tool_name=name, args={"source": "korgex local"}, result=payload,
            success=True, duration_ms=0, triggered_by=None)
    except Exception:
        pass


def cmd_local():
    """Recommend (and optionally wire) a LOCAL model that fits this machine.

    `korgex local`                 → hardware-aware recommendations (via llmfit)
    `korgex local --use <tag>`     → set that Ollama model as the default + record it
    `korgex local --use-case X`    → bias recommendations (coding|reasoning|chat)
    """
    from src import local_model as LM
    from src import config as C

    argv = sys.argv[1:]
    rest = argv[argv.index("local") + 1:] if "local" in argv else []
    use_model, use_case = None, "coding"
    i = 0
    while i < len(rest):
        if rest[i] == "--use" and i + 1 < len(rest):
            use_model = rest[i + 1]
            i += 2
            continue
        if rest[i] == "--use-case" and i + 1 < len(rest):
            use_case = rest[i + 1]
            i += 2
            continue
        i += 1

    # Wire a specific local model — no llmfit needed.
    if use_model:
        cfg = C.load_config()
        LM.set_local_model(cfg, use_model)
        C.save_config(cfg)
        _record_local("local.model_set", {"model": cfg.default_model})
        tag = use_model.split("/")[-1]
        print(f"  ✓ default model → {cfg.default_model}  (local, via Ollama)")
        print(f"    if it isn't pulled yet:  ollama pull {tag}")
        return 0

    # Advise: needs llmfit (optional, never bundled).
    if not LM.llmfit_available():
        print("  llmfit isn't installed — it's what sizes a local model to your hardware.")
        print("  get it:  https://github.com/AlexsJones/llmfit")
        print("  already know the model?  korgex local --use <ollama-tag>")
        return 1
    system = LM.parse_system(LM.run_llmfit(["--json", "system"]) or {})
    recs = LM.parse_recommendations(
        LM.run_llmfit(["recommend", "--json", "--use-case", use_case, "--limit", "5"]) or {})
    print(LM.format_advice(recs, system))
    _record_local("local.advice", {"system": system, "top": recs[:3], "use_case": use_case})
    if recs and recs[0].get("name"):
        tag = recs[0]["name"].split("/")[-1].lower()
        print(f"\n  wire it:  ollama pull {tag}  &&  korgex local --use {tag}")
    return 0


def cmd_commands():
    """List available custom slash commands (built-in, project, and user)."""
    from src.commands import default_command_roots, load_commands

    reg = load_commands(default_command_roots(os.getcwd()))
    names = reg.names()
    if not names:
        print("  No commands found.")
        print("  Add markdown commands in .korgex/commands/ or ~/.korgex/commands/.")
        return 0
    print(f"  {len(names)} command(s) — invoke in the REPL as /<name>:\n")
    for n in names:
        c = reg.get(n)
        hint = f"  {c.argument_hint}" if c.argument_hint else ""
        print(f"  /{n}{hint}")
        if c.description:
            print(f"      {c.description}")
    return 0


def cmd_sessions():
    """List recent korgex sessions in this repo's ledger (resume one with `korgex --resume`)."""
    from src.resume import list_sessions

    argv = sys.argv[1:]
    path = None
    if "sessions" in argv:
        rest = [a for a in argv[argv.index("sessions") + 1:] if not a.startswith("-")]
        if rest:
            path = rest[0]
    path = path or os.environ.get("KORG_JOURNAL_PATH", str(Path(".korg") / "journal.jsonl"))

    if not Path(path).exists():
        print(f"  No ledger journal at {path}")
        return 1
    sessions = list_sessions(path)
    if not sessions:
        print(f"  No marked sessions yet in {path}")
        print("  (sessions are recorded going forward; older runs predate the marker)")
        return 0
    print(f"  {len(sessions)} session(s) in {path} — most recent last:\n")
    for s in sessions:
        fp = (s.get("first_prompt") or "").strip()
        fp = fp[:60] + "…" if len(fp) > 60 else fp
        print(f"  {s.get('session_id') or '?'}  {s.get('started_at') or ''}  "
              f"[{s.get('model') or ''}]  {s.get('turns') or 0} turn(s)")
        if fp:
            print(f"      ↳ {fp}")
    print('\n  resume the latest:  korgex --resume "<next task>"')
    return 0


def cmd_repl(resume=False):
    """Start an interactive korgex session (the conversational coding agent)."""
    from src.repl import Repl
    Repl(resume=resume).run()
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
    "trace":             cmd_trace,
    "why":               cmd_why,
    "cost":              cmd_cost,
    "drift":             cmd_drift,
    "import":            cmd_import,
    "audit":             cmd_audit,
    "trajectory":        cmd_trajectory,
    "diag":              cmd_diag,
    "bus":               cmd_bus,
    "mcp":               cmd_mcp,                 # add/list/remove MCP servers
    "mcp-server":        cmd_mcp_server,
    "setup":             cmd_setup,               # connect model providers
    "local":             cmd_local,               # recommend/wire a local model (llmfit)
    "skills":            cmd_skills,              # print available skills
    "sessions":          cmd_sessions,            # list recent sessions (resume with --resume)
    "commands":          cmd_commands,            # list custom slash commands
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

    resume_preamble = None
    if resume:
        from src import resume as _resume
        journal = os.environ.get("KORG_JOURNAL_PATH") or os.path.join(".korg", "journal.jsonl")
        ctx = _resume.build_resume_context(journal)
        if not ctx["found"]:
            print(f"korgex: no prior session to resume in {journal}. "
                  f"Run a task first, or see `korgex sessions`.", file=sys.stderr)
            return 2
        resume_preamble = _resume.resume_preamble(ctx)
        if not quiet:
            sid = ctx.get("session_id") or "previous"
            print(f"↻ resuming {sid} — replaying {ctx['turns']} prior turn(s) from the ledger",
                  file=sys.stderr)

    try:
        agent = KorgexAgent(model=model, mode=mode,
                              interactive=interactive, load_mcp=mcp)
        agent.mark_session_start()
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
        result = agent.run_task(prompt, output_schema=output_schema,
                                resume_context=resume_preamble)
    except RuntimeError as e:
        print(f"korgex: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        from src.errors import humanize_error
        print(f"korgex: {humanize_error(e)}", file=sys.stderr)
        return 2

    text = (result or {}).get("result", "")
    if _should_emit_final(text, getattr(agent, "interactive", None)):
        print(text)
    return 0 if (result or {}).get("success", False) else 1


def _should_emit_final(text: str, interactive) -> bool:
    """Print the agent's final text iff it wasn't already streamed live. The
    naked-prompt path doesn't stream (interactive is None/False), so the result
    must be emitted here — otherwise `korgex "task"` prints nothing."""
    return bool(text) and not interactive


_DESCRIPTION = ("Korgex — autonomous coding agent. "
                "Pass a naked prompt to run the agent, or use a subcommand.")

_EPILOG = ("Examples:\n"
           "  korgex \"fix the auth bug\"     # run the agent on a task\n"
           "  korgex serve                    # start dashboard + open VS Code\n"
           "  korgex dashboard                # start dashboard only\n"
           "  korgex init                     # scaffold an AGENTS.md for this project\n"
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
        elif name == "trace":
            sp.add_argument("path", nargs="?",
                            help="Journal JSONL to trace "
                                 "(default: $KORG_JOURNAL_PATH or .korg/journal.jsonl)")
        elif name == "why":
            sp.add_argument("file", help="the file/path to explain")
            sp.add_argument("journal", nargs="?",
                            help="journal JSONL (default: $KORG_JOURNAL_PATH or .korg/journal.jsonl)")
        elif name == "cost":
            sp.add_argument("path", nargs="?",
                            help="journal JSONL (default: $KORG_JOURNAL_PATH or .korg/journal.jsonl)")
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
        elif name == "trajectory":
            sp.add_argument("journal", nargs="?",
                            help="korg-ledger journal to export (default: $KORG_JOURNAL_PATH)")
            sp.add_argument("--out", "-o",
                            help="append the trajectory here (default: <journal>.trajectory.jsonl)")
        elif name == "diag":
            sp.add_argument("file", nargs="?", help="source file to check with its language server")
        elif name == "bus":
            sp.add_argument("args", nargs="*", help="<send|inbox|history|members> …")
        elif name == "mcp":
            sp.add_argument("args", nargs="*",
                            help="<list|add|remove> … (e.g. add <name> --url <url> | --command <cmd>)")
        elif name == "local":
            sp.add_argument("--use", metavar="OLLAMA_TAG",
                            help="set this Ollama model as the default (e.g. qwen2.5-coder:7b)")
            sp.add_argument("--use-case", choices=["coding", "reasoning", "chat"],
                            help="bias recommendations (default: coding)")
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

    # Handle --version / -V before any other parsing.
    if '--version' in argv or '-V' in argv:
        print(_get_version())
        sys.exit(0)

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

    # Bare `korgex` on a real terminal → launch the interactive REPL (the
    # conversational agent). Piped/redirected (non-TTY) or explicit -h/--help
    # still print help, so scripts and CI never hang on a readline loop.
    if argv == [] and sys.stdout.isatty() and sys.stdin.isatty():
        return cmd_repl()

    # `mcp` and `bus` take free-form/flagged args and read sys.argv themselves;
    # dispatch them directly so the strict subcommand parser doesn't reject their
    # --flags (e.g. `mcp add api --url …`).
    if argv and argv[0] in ("mcp", "bus"):
        return SUBCOMMANDS[argv[0]]() or 0

    if is_subcommand or is_help_only:
        args = _build_subcommand_parser().parse_args(argv)
        if not args.command:
            _build_subcommand_parser().print_help()
            return 0
        return SUBCOMMANDS[args.command]() or 0

    args = _build_prompt_parser().parse_args(argv)
    if not args.prompt_words:
        if args.resume:
            return cmd_repl(resume=True)
        _build_subcommand_parser().print_help()
        return 0
    return run_agent_shim(" ".join(args.prompt_words),
                          model=args.model, resume=args.resume,
                          mode=args.mode, mcp=args.mcp, quiet=args.quiet,
                          output_schema_path=args.output_schema, effort=args.effort)


if __name__ == "__main__":
    sys.exit(main())