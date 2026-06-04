"""korgex recall <query> — surface lean, verified context from the ledger."""
from src import cli
from src import korg_ledger as KL


def _journal(tmp_path):
    jp = str(tmp_path / "j.jsonl")
    c = KL.LocalJournalClient(journal_path=jp)
    root = c.record_user_prompt("add a healthz endpoint to the api")
    c.record_tool_call("Edit", {"file_path": "src/api/health.py"}, {"ok": True}, True, 10, triggered_by=root)
    c.record_tool_call("Edit", {"file_path": "src/billing.py"}, {"ok": True}, True, 8, triggered_by=root)
    return jp


def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["korgex", "recall", *args])
    return cli.cmd_recall()


def test_recall_prints_relevant_and_omits_unrelated(tmp_path, monkeypatch, capsys):
    rc = _run(monkeypatch, "health", "--journal", _journal(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0
    assert "src/api/health.py" in out          # relevant edit surfaced
    assert "src/billing.py" not in out          # unrelated edit omitted


def test_recall_requires_a_query(monkeypatch):
    assert _run(monkeypatch) == 2


def test_recall_missing_journal_is_clean(tmp_path, monkeypatch):
    assert _run(monkeypatch, "health", "--journal", str(tmp_path / "nope.jsonl")) == 1


def test_recall_accepts_multiword_query(tmp_path, monkeypatch, capsys):
    # all positionals form the query; the journal comes via --journal
    rc = _run(monkeypatch, "healthz", "endpoint", "--journal", _journal(tmp_path))
    assert rc == 0
    assert 'asked:' in capsys.readouterr().out  # the matching prompt is in the block
