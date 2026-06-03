"""The REPL self-improvement paths must feed the verifiable ledger.

The unit shapes live in test_skill_ledger.py; this pins the WIRING — most importantly
that a turn's causal seq reaches the learning pass, so a learned skill can be traced
back (`korgex why`) to the prompt that taught it. Losing that link is a silent
audit-quality regression, exactly what a test should catch.
"""
from __future__ import annotations

import io

from src import repl as REPL


def test_run_turn_threads_root_seq_into_learning(monkeypatch, tmp_path):
    r = REPL.Repl(out=io.StringIO())
    r.repo_root = str(tmp_path)

    class _Agent:
        _rewind_sink = None

        def run_task(self, prompt, resume_context=None):
            return {"result": "did the thing", "root_seq": 42}

    a = _Agent()
    monkeypatch.setattr(r, "_ensure_agent", lambda: a)
    r._agent = a

    captured = {}
    monkeypatch.setattr(r, "_learn_from_turn",
                        lambda text, summary, triggered_by=None:
                        captured.update(text=text, summary=summary, triggered_by=triggered_by))

    r._run_turn("do a thing")

    assert captured["triggered_by"] == 42        # the turn's root seq, for `korgex why`
    assert captured["summary"] == "did the thing"
    assert captured["text"] == "do a thing"


def test_ledger_client_helper_is_safe_without_an_agent(tmp_path, monkeypatch):
    # _ledger_client() backs every skill-event record; it must never raise, even with
    # no agent yet (it falls back to the process-default client).
    r = REPL.Repl(out=io.StringIO())
    r.repo_root = str(tmp_path)
    r._agent = None
    client = r._ledger_client()
    # Either a real default client or None — never an exception.
    assert client is None or hasattr(client, "record_tool_call")


def test_skills_log_prints_skill_events_from_the_journal(tmp_path, monkeypatch):
    import json

    journal = tmp_path / ".korg" / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    events = [
        {"seq_id": 1, "tool_name": "user_prompt", "args": {"prompt": "add tests"}, "result": {}},
        {"seq_id": 2, "tool_name": "skill.learned",
         "args": {"name": "run-suite", "action": "create"},
         "result": {"reason": "ran pytest twice"}, "triggered_by": 1},
        {"seq_id": 3, "tool_name": "skill.curated",
         "args": {"merged": ["run-suite"], "removed": ["run-tests"]}, "result": {}, "triggered_by": 1},
    ]
    journal.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(journal))

    out = io.StringIO()
    r = REPL.Repl(out=out)
    r.repo_root = str(tmp_path)
    r.handle(REPL.parse_repl_input("/skills log"))
    s = out.getvalue()

    assert "run-suite" in s                    # the learned skill
    assert "learned" in s                       # the event kind, human-tagged
    assert "ran pytest twice" in s              # the WHY is surfaced
    assert "run-tests" in s                     # the removed duplicate is named
    assert "add tests" not in s                 # non-skill events are filtered out


def test_skills_log_handles_empty_journal(tmp_path, monkeypatch):
    monkeypatch.delenv("KORG_JOURNAL_PATH", raising=False)
    out = io.StringIO()
    r = REPL.Repl(out=out)
    r.repo_root = str(tmp_path)                  # no .korg/journal at all
    r.handle(REPL.parse_repl_input("/skills log"))
    assert "no" in out.getvalue().lower()        # a friendly "nothing yet" message


def test_korgex_why_traces_a_learned_skill_to_its_prompt():
    # `korgex why <skill>` should prove a learned skill's provenance: find the
    # skill.learned event (matched by its name) and trace it back to the prompt
    # that taught it — the same causal walk `why <file>` does for an edit.
    from src.ledger_trace import explain_why
    events = [
        {"seq_id": 1, "tool_name": "user_prompt", "args": {"prompt": "add tests for the parser"}},
        {"seq_id": 2, "tool_name": "skill.learned",
         "args": {"name": "run-suite", "action": "create"},
         "result": {"reason": "ran pytest twice"}, "triggered_by": 1},
    ]
    out = explain_why(events, "run-suite", color=False)
    assert "add tests for the parser" in out      # traced back to the teaching prompt
    assert "skill.learned" in out
    assert "no recorded action" not in out


def test_cmd_skills_log_prints_the_audit_trail(tmp_path, monkeypatch, capsys):
    # `korgex skills log` — the same audit trail as `/skills log`, from the CLI.
    import json

    from src import cli
    journal = tmp_path / "journal.jsonl"
    journal.write_text("\n".join(json.dumps(e) for e in [
        {"seq_id": 1, "tool_name": "user_prompt", "args": {"prompt": "x"}, "result": {}},
        {"seq_id": 2, "tool_name": "skill.learned", "args": {"name": "run-suite", "action": "create"},
         "result": {"reason": "ran it twice"}, "triggered_by": 1},
    ]) + "\n")
    monkeypatch.setattr("sys.argv", ["korgex", "skills", "log", str(journal)])
    rc = cli.cmd_skills()
    out = capsys.readouterr().out
    assert rc == 0
    assert "run-suite" in out
    assert "ran it twice" in out
