"""Cross-vendor end-to-end FLOW PROOF (the flagship GTM narratives, as tests).

These exercise the two flows the go-to-market story depends on, through the real
`korgex` CLI entry points — not by calling internals — and assert the artifacts a
skeptic actually receives are tamper-evident and re-verify:

  FLOW 1  Claude Code session  →  `korgex audit --html`  (import → korg-ledger@v1
          chain → HTML report)  →  `korgex verify`  (re-check the chain on disk).
          The keystone assertion: the embedded JS verifier in the HTML report —
          the exact algorithm a recipient runs in their browser — reproduces the
          Python chain's tip byte-for-byte, AND localizes a tampered event. The
          report is a *proof*, not a screenshot of a claim.

  FLOW 2  A witness tap on a tool-dispatch loop  →  `korgex verify`  →  the same
          self-verifying HTML report. A live production dispatcher becomes a
          verifiable chain with two lines of glue.

Each flow runs end-to-end on a synthetic session so the proof is reproducible in
CI with no fixtures. The runbook in docs/flow-runbook.md captures the exact
commands these tests encode.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src import audit_report as AR  # noqa: E402
from src import ledger_spec as S  # noqa: E402

JS_ASSET = REPO / "src" / "assets" / "korg_verify.js"


# ── A synthetic Claude Code session (the logs a user already has on disk) ────

CLAUDE_CODE_SESSION = [
    {"type": "user", "uuid": "u1", "parentUuid": None,
     "message": {"role": "user", "content": "Add a --dry-run flag to the deploy script."}},
    {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
     "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [
         {"type": "text", "text": "I'll read the script, then add the flag."},
         {"type": "tool_use", "id": "tr1", "name": "Read",
          "input": {"file_path": "deploy.py"}}]}},
    # a tool_result-only user turn (no prose) — must be skipped by the adapter
    {"type": "user", "uuid": "u2", "parentUuid": "a1",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "tr1", "content": "...file contents..."}]}},
    {"type": "assistant", "uuid": "a2", "parentUuid": "u2",
     "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [
         {"type": "text", "text": "Adding the flag and a guard."},
         {"type": "tool_use", "id": "te1", "name": "Edit",
          "input": {"file_path": "deploy.py", "old_string": "def main():",
                    "new_string": "def main(dry_run=False):"}},
         {"type": "tool_use", "id": "tb1", "name": "Bash",
          "input": {"command": "python -m pytest tests/test_deploy.py -q"}}]}},
]


def _write_session(tmp_path) -> Path:
    """Lay out a ~/.claude/projects-style transcript and return its sessions root."""
    proj = tmp_path / "projects" / "deploy-tool"
    proj.mkdir(parents=True)
    (proj / "session.jsonl").write_text(
        "\n".join(json.dumps(line) for line in CLAUDE_CODE_SESSION) + "\n")
    return tmp_path / "projects"


def _run_cli(argv, monkeypatch, capsys) -> tuple[int, str]:
    """Invoke `korgex <argv...>` through the real CLI main() and capture stdout."""
    from src import cli
    monkeypatch.setattr(cli.sys, "argv", ["korgex", *argv])
    rc = cli.main()
    return rc, capsys.readouterr().out


def _events_from(path: Path) -> list:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _run_embedded_js_verifier(events: list) -> dict:
    """Run the EXACT JS the HTML report ships over `events`; return {tip, errors}.

    This is what a skeptic's browser does. It must agree byte-for-byte with the
    Python chain — that agreement is the entire cross-language guarantee.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to exercise the report's in-browser verifier")
    driver = textwrap.dedent(
        f"""
        const v = require({json.dumps(str(JS_ASSET))});
        const events = {json.dumps(events)};
        (async () => {{
          const errors = await v.verifyChain(events);
          let tip = v.GENESIS;
          for (const e of events) tip = await v.chainHash(e);
          process.stdout.write(JSON.stringify({{ tip, errors }}));
        }})();
        """
    )
    out = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


# ── FLOW 1: Claude Code session → audit --html → verify ─────────────────────

def test_flow1_audit_produces_a_verifiable_chain_and_self_verifying_report(
        tmp_path, monkeypatch, capsys):
    """End-to-end: the real `korgex audit --html` on a Claude Code session yields
    a journal that `korgex verify` accepts AND an HTML report whose own embedded
    verifier reproduces the Python tip and flags tampering."""
    root = _write_session(tmp_path)
    journal = tmp_path / "audit.korg.jsonl"
    report = tmp_path / "audit.html"

    # 1) korgex audit --root <root> --out <journal> --html <report>
    rc, out = _run_cli(
        ["audit", "--root", str(root), "--out", str(journal), "--html", str(report)],
        monkeypatch, capsys)
    assert rc == 0, out
    low = out.lower()
    assert "intact" in low and "ledger events" in low
    assert report.name in out  # the CLI points the user at the shareable report

    # the journal is a well-formed korg-ledger@v1 chain + causal DAG
    events = _events_from(journal)
    assert events, "audit produced no events"
    assert S.verify_chain(events) == [] and S.verify_dag(events) == []

    # the adapter dropped the tool_result-only user turn but kept the real tools
    tools = [e["tool_name"] for e in events]
    assert tools.count("user_prompt") == 1            # only the one prose prompt
    assert {"Read", "Edit", "Bash"}.issubset(set(tools))

    # 2) korgex verify <journal>  — the chain re-verifies on disk via the CLI
    rc, vout = _run_cli(["verify", str(journal)], monkeypatch, capsys)
    assert rc == 0, vout
    assert "intact" in vout.lower() and "hash-chain verified" in vout.lower()

    # 3) the HTML report exists, is self-contained, and embeds every event
    assert report.exists()
    html = report.read_text()
    assert "<html" in html.lower()
    assert "<script src" not in html, "report must be a single self-contained file"
    assert "verifyChain" in html and "chainHash" in html
    for e in events:
        assert e["entry_hash"] in html

    # 4) KEYSTONE — the report's OWN embedded verifier (what the recipient runs)
    #    reproduces the Python chain tip byte-for-byte and reports zero errors.
    js = _run_embedded_js_verifier(events)
    assert js["errors"] == [], js["errors"]
    assert js["tip"] == events[-1]["entry_hash"], "JS tip must equal the Python tip"


def test_flow1_report_localizes_tampering_the_skeptic_can_feel(
        tmp_path, monkeypatch, capsys):
    """If anyone doctors one event, the SAME embedded verifier the report ships
    flips to TAMPERED and pinpoints the broken seq — the visceral proof."""
    root = _write_session(tmp_path)
    journal = tmp_path / "audit.korg.jsonl"
    rc, _ = _run_cli(["audit", "--root", str(root), "--out", str(journal)],
                     monkeypatch, capsys)
    assert rc == 0

    events = _events_from(journal)
    target = events[1]  # doctor a mid-chain event, leave its entry_hash stale
    target["args"] = dict(target.get("args", {}), _injected="an attacker edited this")

    # Python verifier catches it...
    py_errs = S.verify_chain(events)
    assert py_errs and any(str(target["seq_id"]) in str(e) for e in py_errs)

    # ...and so does the exact JS the report renders into the recipient's browser.
    js = _run_embedded_js_verifier(events)
    assert js["errors"], "the embedded verifier must catch tampering"
    assert any(e.get("seq") == target["seq_id"] for e in js["errors"]), js["errors"]


# ── FLOW 2: witness tap on a tool-dispatch loop → verify → report ───────────

def _witness_module():
    wpath = REPO / "integrations" / "witness"
    if str(wpath) not in sys.path:
        sys.path.insert(0, str(wpath))
    import witness  # noqa: PLC0415
    return witness


def test_flow2_witness_tap_yields_a_verifiable_chain_and_report(
        tmp_path, monkeypatch, capsys):
    """A witness tap wrapped around a tool-dispatch loop records a tamper-evident
    chain that `korgex verify` accepts and the HTML report re-verifies."""
    witness = _witness_module()
    journal = tmp_path / "pipeline.korg.jsonl"

    # wrap a dispatcher in two lines, exactly as a production integration would
    def handle_tool(name, arguments):
        return {"fetch": {"records": 1280}, "transform": {"dropped": 100},
                "report": {"path": "/tmp/out.csv"}}.get(name, {"ok": True})

    dispatch = witness.tap(handle_tool, journal_path=str(journal))
    assert dispatch("fetch", {"url": "https://data.example", "limit": 1280})["records"] == 1280
    dispatch("transform", {"rule": "dedupe"})
    dispatch("report", {"format": "csv"})

    events = _events_from(journal)
    assert [e["tool_name"] for e in events] == ["fetch", "transform", "report"]

    # the vendored tap's chain verifies under korgex's CANONICAL ledger_spec
    assert S.verify_chain(events) == [] and S.verify_dag(events) == []

    # `korgex verify <journal>` accepts it through the real CLI
    rc, vout = _run_cli(["verify", str(journal)], monkeypatch, capsys)
    assert rc == 0, vout
    assert "intact" in vout.lower()

    # the same self-verifying report renders, and its embedded JS reproduces the tip
    html = AR.render_html(events, {"session": "witness data pipeline", "vendor": "witness"})
    assert "witness data pipeline" in html  # ASCII session name embeds verbatim
    js = _run_embedded_js_verifier(events)
    assert js["errors"] == [] and js["tip"] == events[-1]["entry_hash"]


def test_flow2_witness_import_path_also_verifies(tmp_path, monkeypatch, capsys):
    """The second on-ramp: an EXISTING witness journal (no live tap) imported via
    `korgex import witness` is re-chained and verifies end-to-end."""
    raw = tmp_path / "witness.jsonl"
    raw.write_text("\n".join(json.dumps(e) for e in [
        {"id": "ev-1", "tool": "fetch", "action": "download", "target": "d.jsonl",
         "artifact_hash": "a" * 64, "metadata": {"rows": 1280}, "parent_id": None},
        {"id": "ev-2", "tool": "transform", "action": "dedupe", "target": "d.jsonl",
         "metadata": {"dropped": 100}, "parent_id": "ev-1"},
    ]) + "\n")
    out = tmp_path / "witness.korg.jsonl"

    rc, iout = _run_cli(["import", "witness", str(raw), "--out", str(out)],
                        monkeypatch, capsys)
    assert rc == 0, iout
    assert "verified" in iout.lower()

    events = _events_from(out)
    assert events[1]["triggered_by"] == 1  # parent_id lineage carried onto the chain
    assert S.verify_chain(events) == [] and S.verify_dag(events) == []

    rc, vout = _run_cli(["verify", str(out)], monkeypatch, capsys)
    assert rc == 0 and "intact" in vout.lower()
