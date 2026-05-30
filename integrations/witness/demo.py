#!/usr/bin/env python3.11
"""End-to-end demo of the witness tap on a (mock) tool-dispatch session.

Uses stand-in tools — no real modules, no real data — to show the full value: a
tapped dispatch produces a tamper-evident, verifiable journal, and that journal
renders into a shareable self-verifying audit report.

    python3.11 integrations/witness/demo.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))   # witness
sys.path.insert(0, str(REPO))   # korgex: src.ledger_spec / src.audit_report

import witness  # noqa: E402
from src import audit_report, ledger_spec  # noqa: E402

JOURNAL = "/tmp/witness_demo.korg.jsonl"
REPORT = "/tmp/witness_demo.html"


def handle_tool(name, arguments):
    """Stand-in for any dispatcher — returns plausible tool output."""
    return {
        "fetch": {"records": 1280, "source": arguments.get("url", "")},
        "parse": {"rows": 1280, "errors": 0},
        "transform": {"rows_out": 1180, "dropped": 100},
        "validate": {"ok": True, "schema": "v3"},
        "report": {"path": "/tmp/out.csv"},
    }.get(name, {"ok": True})


def main():
    if Path(JOURNAL).exists():
        Path(JOURNAL).unlink()
    dispatch = witness.tap(handle_tool, journal_path=JOURNAL)

    # a small session: fetch → parse → transform → validate → report
    dispatch("fetch", {"url": "https://data.example/dataset", "limit": 1280})
    dispatch("parse", {"format": "jsonl"})
    dispatch("transform", {"rule": "dedupe"})
    dispatch("validate", {"schema": "v3"})
    dispatch("report", {"format": "csv"})

    events = [json.loads(ln) for ln in open(JOURNAL) if ln.strip()]
    errs = ledger_spec.verify_chain(events) + ledger_spec.verify_dag(events)
    Path(REPORT).write_text(audit_report.render_html(
        events, {"session": "witness demo · data pipeline", "vendor": "witness"}))

    print(f"  tapped {len(events)} tool calls → {JOURNAL}")
    print(f"  chain:  {'✓ INTACT — tamper-evident' if not errs else '✗ ' + str(errs)}")
    print(f"  report: {REPORT}  ← open in any browser; it re-verifies itself")
    return 0 if not errs else 1


if __name__ == "__main__":
    raise SystemExit(main())
