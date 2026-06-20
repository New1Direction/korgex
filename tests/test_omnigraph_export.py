import json
import stat
import sys

from src import korg_ledger as KL
from src import omnigraph_export as OG
from src import receipt as RC
from src import signing


def _journal(tmp_path):
    path = tmp_path / "journal.jsonl"
    client = KL.LocalJournalClient(journal_path=str(path))
    root = client.record_user_prompt("fix secret bug with TOKEN=abc123")
    client.record_tool_call(
        "Edit",
        {"file_path": "src/app.py", "old": "secret old", "new": "secret new"},
        {"ok": True, "diff": "secret diff"},
        True,
        7,
        triggered_by=root,
    )
    return path


def _jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_export_records_are_omnigraph_load_shape_and_privacy_preserving(tmp_path, monkeypatch):
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)
    journal = _journal(tmp_path)
    out = tmp_path / "korgex.jsonl"
    schema = tmp_path / "korgex.pg"

    summary = OG.export_records(journal, out_path=out, schema_out=schema)

    rows = _jsonl(out)
    assert summary["events"] == 2
    assert summary["files"] == 1
    assert any(r.get("type") == "KorgexRun" for r in rows)
    assert any(r.get("type") == "KorgexEvent" for r in rows)
    assert any(r.get("type") == "KorgexFile" for r in rows)
    assert any(r.get("edge") == "RunHasEvent" for r in rows)
    assert any(r.get("edge") == "EventTouchedFile" for r in rows)
    assert any(r.get("edge") == "EventTriggered" for r in rows)
    assert "node KorgexRun" in schema.read_text()

    raw = out.read_text()
    assert "TOKEN=abc123" not in raw
    assert "secret old" not in raw
    assert "secret new" not in raw
    assert "secret diff" not in raw
    assert "src/app.py" in raw  # file lineage is intentionally queryable


def test_export_signed_receipt_preserves_claim_and_signer(tmp_path, monkeypatch):
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)
    events = KL.load_journal_raw(str(_journal(tmp_path)))
    priv, _ = signing.generate_keypair()
    rec = RC.build_receipt(events, claim="fixed issue #123", signer_priv=priv, generated_at=1.0)
    rec_path = tmp_path / "receipt.json"
    rec_path.write_text(json.dumps(rec))
    out = tmp_path / "receipt.jsonl"

    summary = OG.export_records(rec_path, out_path=out)

    run = next(r["data"] for r in _jsonl(out) if r.get("type") == "KorgexRun")
    assert summary["source_kind"] == "receipt"
    assert run["claim"] == "fixed issue #123"
    assert run["signed_by"] == rec["signature"]["pubkey"]
    assert run["generated_at"] == "1.0"


def test_write_to_omnigraph_invokes_omnigraph_load(tmp_path, monkeypatch):
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)
    journal = _journal(tmp_path)
    fake = tmp_path / "omnigraph"
    log = tmp_path / "argv.json"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'w').write(json.dumps(sys.argv))\n"
        "print('loaded')\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    summary = OG.write_to_omnigraph(
        journal,
        store="devgraph.omni",
        branch="agent/issue-123",
        base="main",
        tmp_out=tmp_path / "export.jsonl",
        omnigraph_bin=str(fake),
    )

    argv = json.loads(log.read_text())
    assert argv[:2] == [str(fake), "load"]
    assert "--data" in argv and str(tmp_path / "export.jsonl") in argv
    assert "--mode" in argv and "append" in argv
    assert "--branch" in argv and "agent/issue-123" in argv
    assert "--from" in argv and "main" in argv
    assert argv[-1] == "devgraph.omni"
    assert summary["returncode"] == 0


def test_cli_omnigraph_export_writes_files(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)
    journal = _journal(tmp_path)
    out = tmp_path / "out.jsonl"
    schema = tmp_path / "schema.pg"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "korgex",
            "omnigraph",
            "export",
            str(journal),
            "--out",
            str(out),
            "--schema-out",
            str(schema),
        ],
    )

    from src.cli import cmd_omnigraph

    assert cmd_omnigraph() == 0
    assert out.exists()
    assert schema.exists()
    printed = capsys.readouterr().out
    assert "Omnigraph export ready" in printed
    assert str(out) in printed
