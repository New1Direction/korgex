"""Session resume (A) — rebuild a readable transcript from the verifiable journal.

The promise: korgex records every turn (user prompt, the model's reply text, tool calls +
results) to the korg-ledger, so resume can replay that record back into context. These tests
pin the reconstruction: session markers, scoping to the right session, the fallback for old
journals without markers, transcript rendering, and budget truncation.
"""
from __future__ import annotations

from src import resume as R
from src.korg_ledger import LocalJournalClient, load_journal_raw


def _client(tmp_path):
    return LocalJournalClient(journal_path=str(tmp_path / "journal.jsonl"))


def _session(client, prompts_and_replies, cwd="/repo", model="claude-sonnet-4-6"):
    """Mark a session start, then record (prompt, reply, [tool]) turns under it."""
    sid = R.mark_session_start(client, cwd=cwd, model=model)
    for turn in prompts_and_replies:
        client.record_user_prompt(turn["prompt"])
        client.record_llm_call(model, 10, 5, 50, None, assistant_text=turn.get("reply"))
        for t in turn.get("tools", []):
            client.record_tool_call(t[0], t[1], t[2], True, 1)
    return sid


def test_mark_session_start_records_marker_that_survives_redaction(tmp_path):
    c = _client(tmp_path)
    sid = R.mark_session_start(c, cwd="/repo", model="claude-sonnet-4-6")
    assert sid and sid.startswith("sess_")
    events = load_journal_raw(str(tmp_path / "journal.jsonl"))
    starts = [e for e in events if e.get("tool_name") == R.SESSION_START]
    assert len(starts) == 1
    # the id, cwd, model must round-trip through redact() unmangled
    args = starts[0]["args"]
    assert args["session_id"] == sid
    assert args["cwd"] == "/repo" and args["model"] == "claude-sonnet-4-6"


def test_mark_session_start_no_ledger_is_safe():
    assert R.mark_session_start(None, cwd="/x", model="m") is None


def test_list_sessions_oldest_to_newest_with_turns_and_first_prompt(tmp_path):
    c = _client(tmp_path)
    _session(c, [{"prompt": "first thing", "reply": "ok"}], cwd="/a", model="m1")
    _session(c, [{"prompt": "second A", "reply": "ok"}, {"prompt": "second B", "reply": "ok"}],
             cwd="/b", model="m2")
    sessions = R.list_sessions(str(tmp_path / "journal.jsonl"))
    assert len(sessions) == 2
    assert sessions[0]["turns"] == 1 and sessions[0]["first_prompt"] == "first thing"
    assert sessions[0]["cwd"] == "/a" and sessions[0]["model"] == "m1"
    assert sessions[1]["turns"] == 2 and sessions[1]["first_prompt"] == "second A"


def test_build_resume_context_scopes_to_the_last_session(tmp_path):
    c = _client(tmp_path)
    _session(c, [{"prompt": "OLD work on the parser", "reply": "old reply"}])
    _session(c, [{"prompt": "NEW add a healthcheck", "reply": "I'll add /healthz",
                  "tools": [("Read", {"file_path": "app.py"}, {"content": "x"})]}])
    ctx = R.build_resume_context(str(tmp_path / "journal.jsonl"))
    assert ctx["found"] is True
    t = ctx["transcript"]
    assert "NEW add a healthcheck" in t and "I'll add /healthz" in t and "Read" in t
    assert "OLD work on the parser" not in t          # scoped to the LAST session only


def test_build_resume_context_by_session_id(tmp_path):
    c = _client(tmp_path)
    first = _session(c, [{"prompt": "OLD parser work", "reply": "r"}])
    _session(c, [{"prompt": "NEW healthcheck", "reply": "r"}])
    ctx = R.build_resume_context(str(tmp_path / "journal.jsonl"), session_id=first)
    assert ctx["session_id"] == first
    assert "OLD parser work" in ctx["transcript"] and "NEW healthcheck" not in ctx["transcript"]


def test_build_resume_context_fallback_when_no_markers(tmp_path):
    # An OLD journal written before session markers existed: no session_start events.
    c = _client(tmp_path)
    c.record_user_prompt("legacy prompt one")
    c.record_llm_call("m", 1, 1, 1, None, assistant_text="legacy reply")
    c.record_user_prompt("legacy prompt two")
    ctx = R.build_resume_context(str(tmp_path / "journal.jsonl"), fallback_turns=12)
    assert ctx["found"] is True
    assert "legacy prompt one" in ctx["transcript"] and "legacy prompt two" in ctx["transcript"]


def test_transcript_respects_char_budget(tmp_path):
    c = _client(tmp_path)
    big = [{"prompt": f"turn {i} " + "x" * 400, "reply": "y" * 400} for i in range(40)]
    _session(c, big)
    ctx = R.build_resume_context(str(tmp_path / "journal.jsonl"), max_chars=2000)
    assert len(ctx["transcript"]) <= 2000 + 60          # budget + the trim marker
    assert "trimmed" in ctx["transcript"]
    assert "turn 39" in ctx["transcript"]                # keeps the MOST RECENT


def test_build_resume_context_empty_or_missing_journal(tmp_path):
    ctx = R.build_resume_context(str(tmp_path / "does-not-exist.jsonl"))
    assert ctx["found"] is False and ctx["transcript"] == ""


def test_resume_preamble_frames_the_transcript(tmp_path):
    c = _client(tmp_path)
    sid = _session(c, [{"prompt": "do the thing", "reply": "done"}])
    ctx = R.build_resume_context(str(tmp_path / "journal.jsonl"))
    pre = R.resume_preamble(ctx)
    assert "RESUMING" in pre.upper() and sid in pre
    assert "do the thing" in pre                          # the transcript is embedded


# ── agent + CLI wiring ────────────────────────────────────────────────────────

class _FakeLedger:
    def __init__(self):
        self.calls = []

    def record_tool_call(self, tool_name, args, result, success, duration_ms, triggered_by=None):
        self.calls.append((tool_name, args))
        return len(self.calls)


def test_agent_mark_session_start_records_marker_once():
    from src.agent import KorgexAgent
    fake = _FakeLedger()
    agent = KorgexAgent(model="claude-sonnet-4-6", repo_root="/repo", ledger=fake)
    sid = agent.mark_session_start()
    assert sid and sid.startswith("sess_")
    assert any(c[0] == R.SESSION_START and c[1]["session_id"] == sid for c in fake.calls)
    # idempotent — a second call doesn't double-record
    assert agent.mark_session_start() == sid
    assert sum(1 for c in fake.calls if c[0] == R.SESSION_START) == 1


def test_cmd_sessions_lists_recent_sessions(tmp_path, monkeypatch, capsys):
    from src import cli
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    c = _client(tmp_path)
    _session(c, [{"prompt": "build the parser", "reply": "ok"}], model="m1")
    _session(c, [{"prompt": "add healthcheck", "reply": "ok"},
                 {"prompt": "and tests", "reply": "ok"}], model="m2")
    monkeypatch.setattr("sys.argv", ["korgex", "sessions"])
    rc = cli.cmd_sessions()
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 session(s)" in out
    assert "build the parser" in out and "add healthcheck" in out
    assert "2 turn(s)" in out


def test_run_agent_shim_resume_injects_prior_context(tmp_path, monkeypatch):
    from src import cli
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "journal.jsonl"))
    c = _client(tmp_path)
    _session(c, [{"prompt": "we were building the auth module", "reply": "started auth.py"}])

    seen = {}

    class _FakeAgent:
        def __init__(self, **kw):
            pass

        def mark_session_start(self):
            seen["marked"] = True

        def run_task(self, prompt, output_schema=None, resume_context=None):
            seen["prompt"] = prompt
            seen["resume_context"] = resume_context
            return {"result": "done", "success": True}

    monkeypatch.setattr("src.agent.KorgexAgent", _FakeAgent)
    rc = cli.run_agent_shim("now finish it", resume=True, quiet=True)
    assert rc == 0
    assert seen.get("marked") is True
    assert seen["prompt"] == "now finish it"
    rc_ctx = seen.get("resume_context") or ""
    assert "we were building the auth module" in rc_ctx and "RESUMING" in rc_ctx.upper()


def test_run_agent_shim_resume_with_no_prior_session_exits_cleanly(tmp_path, monkeypatch, capsys):
    from src import cli
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(tmp_path / "empty.jsonl"))
    rc = cli.run_agent_shim("continue", resume=True, quiet=True)
    assert rc == 2
    assert "no prior session" in capsys.readouterr().err
