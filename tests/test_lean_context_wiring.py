"""Loop-wiring: the agent injects lean, verified ledger context — opt-in, fail-safe.

KORGEX_LEAN_CONTEXT=1 makes the agent retrieve the past ledger events relevant to the
current prompt and add them to the system prompt as a compact, provenance-stamped
block — so a smaller/self-hosted model gets trustworthy context without carrying the
whole history. Off by default; any failure degrades to no block (an enhancement, not
core), exactly like the existing memory-recall step.
"""
from src.agent import KorgexAgent
from src import korg_ledger as KL


def _agent_with_journal(tmp_path):
    a = KorgexAgent(interactive=False)
    a.repo_root = str(tmp_path)
    jp = tmp_path / ".korg" / "journal.jsonl"
    jp.parent.mkdir(parents=True, exist_ok=True)
    c = KL.LocalJournalClient(journal_path=str(jp))
    root = c.record_user_prompt("add a healthz endpoint to the api")
    c.record_tool_call("Edit", {"file_path": "src/api/health.py"}, {"ok": True}, True, 10, triggered_by=root)
    c.record_tool_call("Edit", {"file_path": "src/billing.py"}, {"ok": True}, True, 8, triggered_by=root)
    return a


def test_lean_block_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("KORGEX_LEAN_CONTEXT", raising=False)
    monkeypatch.delenv("KORG_JOURNAL_PATH", raising=False)
    a = _agent_with_journal(tmp_path)
    assert a._lean_context_block("health") == ""          # opt-in: silent unless enabled


def test_lean_block_on_injects_relevant_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_LEAN_CONTEXT", "1")
    monkeypatch.delenv("KORG_JOURNAL_PATH", raising=False)
    a = _agent_with_journal(tmp_path)
    block = a._lean_context_block("health")
    assert "src/api/health.py" in block                   # relevant prior work surfaced
    assert "src/billing.py" not in block                  # unrelated work left out
    assert "#" in block                                   # provenance seq handles present


def test_lean_block_missing_journal_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_LEAN_CONTEXT", "1")
    monkeypatch.delenv("KORG_JOURNAL_PATH", raising=False)
    a = KorgexAgent(interactive=False)
    a.repo_root = str(tmp_path)                            # no .korg journal here
    assert a._lean_context_block("health") == ""


def test_lean_block_no_match_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_LEAN_CONTEXT", "1")
    monkeypatch.delenv("KORG_JOURNAL_PATH", raising=False)
    a = _agent_with_journal(tmp_path)
    assert a._lean_context_block("nonexistent-zzz-token") == ""
