"""LSP diagnostics promoted from advisory to ENFORCED (opt-in hard-block).

Today the post_tool path folds a language server's findings into the edit result
and records an `lsp.diagnostics` event — but it never vetoes. This gate adds an
opt-in hard-block: when KORGEX_LSP_ENFORCE is on, a Write/Edit that introduces a
SEVERITY-1 (error) diagnostic is REFUSED — the file is reverted to its pre-edit
state and a verifiable `lsp.enforce` policy event is recorded. Default OFF, so
existing flows are unaffected.

Mirrors the edit-approval gate's test pattern (FakeLedger + a direct call to the
helper) so the veto is unit-testable without spinning the whole LLM loop.
"""
from __future__ import annotations

from src.agent import KorgexAgent


class FakeLedger:
    """Captures ledger events instead of writing a journal. Implements the full
    3-method client API so it can back a real run_task loop, not just the helper."""

    def __init__(self):
        self.events = []

    def record_tool_call(self, **kw):
        self.events.append(kw)
        return len(self.events)

    def record_user_prompt(self, prompt, triggered_by=None):
        self.events.append({"tool_name": "user_prompt", "args": {"prompt": prompt},
                            "triggered_by": triggered_by})
        return len(self.events)

    def record_llm_call(self, **kw):
        self.events.append({"tool_name": "llm_inference", **kw})
        return len(self.events)


def _agent(tmp_path, enforce=False):
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    a.lsp_enforce = enforce
    return a


ERR = [{"message": "undefined name 'foo'", "severity": 1, "range": {"start": {"line": 1}}}]
WARN = [{"message": "unused import", "severity": 2}]


# ── flag plumbing ───────────────────────────────────────────────────────────

def test_enforce_flag_is_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("KORGEX_LSP_ENFORCE", raising=False)
    assert KorgexAgent(repo_root=str(tmp_path)).lsp_enforce is False


def test_enforce_flag_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KORGEX_LSP_ENFORCE", "1")
    assert KorgexAgent(repo_root=str(tmp_path)).lsp_enforce is True
    monkeypatch.setenv("KORGEX_LSP_ENFORCE", "off")
    assert KorgexAgent(repo_root=str(tmp_path)).lsp_enforce is False


# ── the veto itself ─────────────────────────────────────────────────────────

def test_no_veto_when_enforcement_off(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("bad = code\n")
    a = _agent(tmp_path, enforce=False)
    led = FakeLedger()
    call = {"name": "Write", "args": {"file_path": str(f)}}
    block = a._lsp_enforce_block(call, ERR, led, llm_seq=1, pre_content="prev\n")
    assert block is None                  # advisory mode → no veto
    assert led.events == []               # and no enforce event
    assert f.read_text() == "bad = code\n"  # file left as-is


def test_no_veto_when_only_warnings(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("import os\n")
    a = _agent(tmp_path, enforce=True)
    led = FakeLedger()
    call = {"name": "Edit", "args": {"file_path": str(f)}}
    assert a._lsp_enforce_block(call, WARN, led, llm_seq=1, pre_content="x\n") is None
    assert led.events == []                # severity-2 is not an error → no block
    assert f.read_text() == "import os\n"  # untouched


def test_veto_on_severity_one_reverts_existing_file_and_records(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("foo()\n")              # the edit that introduced the error
    a = _agent(tmp_path, enforce=True)
    led = FakeLedger()
    call = {"name": "Write", "args": {"file_path": str(f)}}
    block = a._lsp_enforce_block(call, ERR, led, llm_seq=7,
                                 pre_content="# original\n")
    # the call is refused with a fix-or-revert message
    assert block is not None
    assert block["verdict"] == "LSP_SEVERITY_1"
    assert "fix" in block["error"].lower() or "revert" in block["error"].lower()
    # the file is reverted to its pre-edit content (the bad edit is undone)
    assert f.read_text() == "# original\n"
    # and a verifiable policy event is recorded, triggered_by the llm seq
    ev = led.events[-1]
    assert ev["tool_name"] == "lsp.enforce"
    assert ev["success"] is False
    assert ev["triggered_by"] == 7
    assert ev["result"]["error_count"] == 1
    assert ev["result"]["action"] == "reverted"


def test_veto_deletes_a_newly_created_file_on_revert(tmp_path):
    # pre_content is None → the Write CREATED the file; reverting means deleting it.
    f = tmp_path / "new.py"
    f.write_text("syntax error here\n")
    a = _agent(tmp_path, enforce=True)
    led = FakeLedger()
    call = {"name": "Write", "args": {"file_path": str(f)}}
    block = a._lsp_enforce_block(call, ERR, led, llm_seq=1, pre_content=None)
    assert block is not None
    assert not f.exists()                 # the newly created bad file is gone
    assert led.events[-1]["result"]["action"] == "reverted"


def test_no_veto_for_non_mutating_tool(tmp_path):
    a = _agent(tmp_path, enforce=True)
    led = FakeLedger()
    call = {"name": "Read", "args": {"file_path": str(tmp_path / "a.py")}}
    assert a._lsp_enforce_block(call, ERR, led, llm_seq=1, pre_content=None) is None
    assert led.events == []


def test_no_veto_when_no_diagnostics(tmp_path):
    a = _agent(tmp_path, enforce=True)
    led = FakeLedger()
    call = {"name": "Write", "args": {"file_path": str(tmp_path / "a.py")}}
    assert a._lsp_enforce_block(call, [], led, llm_seq=1, pre_content=None) is None
    assert led.events == []


# ── full-loop wiring: the veto reaches the model + reverts on disk ──────────
# Drives run_task end-to-end with a scripted LLM (round 1 writes a file that the
# language server flags severity-1; round 2 stops). Proves the post_tool path
# captures pre-content, vetoes, reverts the file, and feeds the block back to
# the model as the tool result — not just that the helper works in isolation.

def _script_llm(agent, scripted_rounds):
    """Patch the agent's provider seam to replay a list of (tool_calls, text)."""
    rounds = iter(scripted_rounds)

    agent._get_client = lambda: object()
    agent._call = lambda *a, **k: next(rounds)
    agent._extract_tool_calls = lambda resp: resp[0]
    agent._extract_final_text = lambda resp: resp[1]
    agent._assistant_turn = lambda resp: {"role": "assistant", "content": resp[1]}
    # usage is read via getattr with defaults — a bare object() yields 0s.


def test_full_loop_vetoes_and_reverts_a_severity_one_write(monkeypatch, tmp_path):
    from src import lsp
    from src.agent import KorgexAgent

    # A language server that always flags a severity-1 error on the written file.
    monkeypatch.setattr(lsp, "diagnostics",
                        lambda p: [{"message": "undefined name 'foo'", "severity": 1}])
    monkeypatch.setenv("KORGEX_LSP_DIAGNOSTICS", "1")  # register the post_tool plugin
    monkeypatch.setenv("KORGEX_LSP_ENFORCE", "1")       # and arm the hard-block

    target = tmp_path / "mod.py"  # does NOT exist yet → a create; revert = delete
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    a.edit_policy = "session"  # don't let the approval gate get in the way
    a.ledger = FakeLedger()

    write_call = [{"id": "t1", "name": "Write",
                   "args": {"file_path": str(target), "content": "foo()\n"}}]
    _script_llm(a, [(write_call, ""), ([], "stopping")])

    a.run_task("write the module")

    # The bad create was reverted (the severity-1 file must not survive on disk).
    assert not target.exists(), "vetoed create should have been reverted (deleted)"
    # A verifiable lsp.enforce REFUSED event was recorded.
    enforce_events = [e for e in a.ledger.events if e["tool_name"] == "lsp.enforce"]
    assert enforce_events, "expected an lsp.enforce policy event"
    assert enforce_events[-1]["result"]["verdict"] == "REFUSED"
    assert enforce_events[-1]["success"] is False


def test_full_loop_does_not_veto_when_enforcement_off(monkeypatch, tmp_path):
    from src import lsp
    from src.agent import KorgexAgent

    monkeypatch.setattr(lsp, "diagnostics",
                        lambda p: [{"message": "undefined name 'foo'", "severity": 1}])
    monkeypatch.setenv("KORGEX_LSP_DIAGNOSTICS", "1")
    monkeypatch.delenv("KORGEX_LSP_ENFORCE", raising=False)  # advisory mode

    target = tmp_path / "mod.py"
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    a.edit_policy = "session"
    a.ledger = FakeLedger()

    write_call = [{"id": "t1", "name": "Write",
                   "args": {"file_path": str(target), "content": "foo()\n"}}]
    _script_llm(a, [(write_call, ""), ([], "stopping")])

    a.run_task("write the module")

    # Advisory: the write SURVIVES (no revert) and diagnostics were still recorded.
    assert target.exists() and target.read_text() == "foo()\n"
    assert any(e["tool_name"] == "lsp.diagnostics" for e in a.ledger.events)
    assert not any(e["tool_name"] == "lsp.enforce" for e in a.ledger.events)
