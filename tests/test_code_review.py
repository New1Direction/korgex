"""korgex review — verifiable code review. A diff is reviewed across dimensions, each
finding is adversarially verified, and confirmed findings are recorded as tamper-
evident `review.finding` ledger events (so `korgex verify`/`trace`/`why` can prove the
review happened + what it found).

The LLM is injected (reviewer/verifier callables), so the core tests fully offline.
"""
from __future__ import annotations

from src import code_review as CR

REVIEW_JSON = """The diff has a couple of issues:
[
  {"dimension": "security", "severity": "high", "file": "src/a.py", "line": 10,
   "title": "SQL injection", "detail": "query built by string concat", "suggestion": "parameterize"},
  {"dimension": "performance", "severity": "low", "file": "src/b.py",
   "title": "N+1 query", "detail": "queries inside a loop"}
]
That's it."""


# ── diff acquisition ──────────────────────────────────────────────────────────

def test_get_diff_three_dot_against_base():
    seen = {}

    def run(cmd):
        seen["cmd"] = cmd
        return (0, "DIFFTEXT", "")

    assert CR.get_diff("main", run=run) == "DIFFTEXT"
    assert seen["cmd"] == ["git", "diff", "main...HEAD"]


def test_get_diff_modes():
    def run(cmd):                                       # echo the command back as the "diff"
        return (0, " ".join(cmd), "")
    assert CR.get_diff("staged", run=run) == "git diff --cached"
    assert CR.get_diff("working", run=run) == "git diff"
    assert CR.get_diff("origin/main", run=run) == "git diff origin/main...HEAD"


# ── parsing ─────────────────────────────────────────────────────────────────

def test_parse_review_reply():
    fs = CR.parse_review_reply(REVIEW_JSON)
    assert len(fs) == 2
    assert fs[0].dimension == "security" and fs[0].severity == "high"
    assert fs[0].file == "src/a.py" and fs[0].line == 10 and fs[0].title == "SQL injection"
    assert fs[1].line is None                          # missing line tolerated


def test_parse_review_reply_garbage():
    assert CR.parse_review_reply("no json here") == []
    assert CR.parse_review_reply("") == []
    assert CR.parse_review_reply("[{}]") == []         # a finding with no title/file is dropped


# ── review orchestration ─────────────────────────────────────────────────────

def test_review_diff_uses_reviewer():
    fs = CR.review_diff("a real diff", reviewer=lambda d: CR.parse_review_reply(REVIEW_JSON))
    assert len(fs) == 2


def test_review_diff_empty_diff_does_not_call_the_model():
    called = {}
    CR.review_diff("   ", reviewer=lambda d: called.setdefault("hit", True) or [])
    assert "hit" not in called


def test_make_reviewer_puts_the_diff_in_the_prompt():
    rec = {}

    def complete(system, user):
        rec["system"], rec["user"] = system, user
        return REVIEW_JSON

    fs = CR.make_reviewer(complete)("DIFF-XYZ-MARKER")
    assert len(fs) == 2
    assert "DIFF-XYZ-MARKER" in rec["user"] and rec["system"]


# ── adversarial verify ────────────────────────────────────────────────────────

def test_verify_findings_marks_confirmed():
    fs = CR.parse_review_reply(REVIEW_JSON)
    out = CR.verify_findings(fs, verifier=lambda f: f.dimension == "security")
    assert out[0].confirmed is True            # security confirmed
    assert out[1].confirmed is False           # perf refuted


def test_parse_verdict_defaults_to_keep_on_unclear():
    assert CR.parse_verdict('{"confirmed": true}') is True
    assert CR.parse_verdict('{"confirmed": false, "reason": "false positive"}') is False
    assert CR.parse_verdict("garbage, no verdict") is True   # don't drop real findings on a glitch


# ── summary + recording ───────────────────────────────────────────────────────

def test_summarize():
    s = CR.summarize(CR.parse_review_reply(REVIEW_JSON))
    assert s["total"] == 2
    assert s["worst"] == "high"
    assert s["by_dimension"]["security"] == 1
    assert s["by_severity"]["low"] == 1


class _CaptureClient:
    def __init__(self):
        self.calls = []

    def record_tool_call(self, **k):
        self.calls.append(k)
        return len(self.calls)


def test_record_review_emits_finding_events():
    fs = CR.verify_findings(CR.parse_review_reply(REVIEW_JSON), verifier=lambda f: True)
    c = _CaptureClient()
    CR.record_review(c, fs, "main", triggered_by=5)
    assert len(c.calls) == 2
    f0 = c.calls[0]
    assert f0["tool_name"] == "review.finding"
    assert f0["args"]["file"] == "src/a.py" and f0["args"]["severity"] == "high"
    assert f0["triggered_by"] == 5
    assert f0["success"] is False              # high severity → flagged as a problem
    assert c.calls[1]["success"] is True       # low severity → not flagged


def test_record_review_handles_none_client():
    assert CR.record_review(None, [], "main") == 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cmd_review_reports_confirmed_and_gates_on_high(monkeypatch, capsys):
    import sys as _sys

    from src import cli
    monkeypatch.setattr(CR, "get_diff", lambda base, **k: "diff --git a/x b/x\n+bad")
    f = CR.Finding("security", "high", "src/a.py", "SQL injection", line=10,
                   suggestion="parameterize", confirmed=True)
    monkeypatch.setattr(CR, "review_diff", lambda diff, reviewer: [f])
    monkeypatch.setattr(CR, "verify_findings", lambda fs, v: list(fs))   # already confirmed

    class _FakeAgent:
        def __init__(self, **k): pass
        def _get_client(self): return object()
        def _call(self, *a, **k): return None
        def _extract_final_text(self, r): return "[]"

    monkeypatch.setattr("src.agent.KorgexAgent", _FakeAgent)
    monkeypatch.setattr("src.korg_ledger.get_default_client", lambda: None)
    monkeypatch.setattr(_sys, "argv", ["korgex", "review", "main"])
    rc = cli.cmd_review()
    out = capsys.readouterr().out
    assert "SQL injection" in out and "src/a.py" in out
    assert rc == 1                              # confirmed high → CI gate


def test_cmd_review_no_changes_exits_zero(monkeypatch, capsys):
    import sys as _sys

    from src import cli
    monkeypatch.setattr(CR, "get_diff", lambda base, **k: "")
    monkeypatch.setattr(_sys, "argv", ["korgex", "review"])
    assert cli.cmd_review() == 0
    assert "no changes to review" in capsys.readouterr().out


def test_korgex_why_traces_a_review_finding_to_its_file():
    from src.ledger_trace import explain_why
    events = [
        {"seq_id": 1, "tool_name": "user_prompt", "args": {"prompt": "add the parser"}},
        {"seq_id": 2, "tool_name": "review.finding",
         "args": {"file": "src/parser.py", "severity": "high", "dimension": "security"},
         "result": {"title": "unsafe eval"}, "triggered_by": 1},
    ]
    out = explain_why(events, "src/parser.py", color=False)
    assert "add the parser" in out and "review.finding" in out
    assert "no recorded action" not in out


# ── real-model-shape tolerance (regression: gpt-4o used different keys → 0 findings) ──

GPT4O_SHAPE = """```json
[
  {"type": "Security", "severity": "High", "message": "Hard-coded API token committed", "line": 4},
  {"type": "Security", "severity": "Medium", "message": "os.system shell injection", "line": 3}
]
```"""


def test_parse_review_reply_tolerates_real_model_shape():
    # gpt-4o returned type/message/no-file + capitalized severity + ```json fences.
    fs = CR.parse_review_reply(GPT4O_SHAPE)
    assert len(fs) == 2                              # was 0 — dropped for missing file/title
    assert fs[0].dimension == "security"            # "type":"Security" → dimension, lowercased
    assert fs[0].severity == "high"                 # "High" normalized
    assert "Hard-coded" in (fs[0].title + fs[0].detail)
    assert fs[0].line == 4


def test_dimension_inferred_when_model_omits_it():
    # the model gave no dimension/type field — infer it from the title keywords
    assert CR.parse_review_reply('[{"severity":"high","title":"Hardcoded API token committed"}]')[0].dimension == "security"
    assert CR.parse_review_reply('[{"severity":"low","title":"N+1 query inside the loop"}]')[0].dimension == "performance"
    assert CR.parse_review_reply('[{"severity":"medium","title":"Off-by-one in the range bound"}]')[0].dimension == "correctness"
    # an explicit dimension/type is still trusted over inference
    assert CR.parse_review_reply('[{"type":"Security","title":"Off-by-one bug"}]')[0].dimension == "security"


def test_review_diff_fills_file_from_single_file_diff():
    diff = ("diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n"
            "@@ -0,0 +1 @@\n+bad = eval(x)")
    fs = CR.review_diff(diff, reviewer=lambda d: CR.parse_review_reply(GPT4O_SHAPE))
    assert fs and all(f.file == "src/x.py" for f in fs)   # inferred from the diff
