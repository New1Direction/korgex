"""
`korgex audit` — the zero-friction adoption wedge (first-principles pick).

Point korg at the Claude Code logs you ALREADY have and get an instant
verifiable, tamper-evident audit of everything your agent did: discover the
sessions, import the latest into a korg-ledger@v1 chained journal, verify it,
and report. No setup, no buy-in — the standard, applied to data you already own.
"""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import import_adapters as IA  # noqa: E402

CC_LINES = [
    {"type": "user", "uuid": "u1", "parentUuid": None,
     "message": {"role": "user", "content": "fix the parser"}},
    {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
     "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [
         {"type": "text", "text": "On it."},
         {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "parser.py"}}]}},
]


def test_discover_finds_sessions_newest_first(tmp_path):
    base = tmp_path / "projects"
    (base / "projA").mkdir(parents=True)
    (base / "projB").mkdir(parents=True)
    old = base / "projA" / "old.jsonl"; old.write_text("{}\n")
    new = base / "projB" / "new.jsonl"; new.write_text("{}\n")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    found = IA.discover_claude_code_sessions(root=str(base))
    assert len(found) == 2
    assert found[0].endswith("new.jsonl")        # newest first


def test_discover_empty_when_none():
    assert IA.discover_claude_code_sessions(root="/nonexistent/path/xyz") == []


def test_cmd_audit_end_to_end(tmp_path, monkeypatch, capsys):
    from src import cli
    base = tmp_path / "projects" / "p"
    base.mkdir(parents=True)
    (base / "s.jsonl").write_text("\n".join(json.dumps(l) for l in CC_LINES) + "\n")
    out = tmp_path / "audit.jsonl"

    monkeypatch.setattr(cli.sys, "argv",
                        ["korgex", "audit", "--root", str(tmp_path / "projects"), "--out", str(out)])
    rc = cli.main()
    text = capsys.readouterr().out.lower()
    assert rc == 0
    assert "intact" in text                       # the audited session verifies
    assert "events" in text
    # the journal it produced is itself verifiable
    from src.ledger_spec import verify_chain, verify_dag
    events = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert events and verify_chain(events) == [] and verify_dag(events) == []


def test_cmd_audit_no_sessions(monkeypatch, capsys):
    from src import cli
    monkeypatch.setattr(cli.sys, "argv", ["korgex", "audit", "--root", "/nonexistent/xyz"])
    assert cli.main() == 1
    assert "no claude code sessions" in capsys.readouterr().out.lower()
