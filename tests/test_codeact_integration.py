"""CodeAct LOOP-LEVEL SAFETY NET — the wire-integration tests (REQUIRED).

Unit-green CodeAct code broke on the wire 3× because the parent<->kernel protocol
only fails when you drive the REAL korgex agent loop against the REAL kernel
subprocess. These tests do exactly that — with a STUBBED provider (no network, no
API key) — and assert the five things the build contract demands:

  (1) a "python" code action runs in the kernel and its result returns to the loop;
  (2) a code action that calls bridged tools (read_file/bash/grep) routes EACH
      sub-call through route_tool_call as its OWN ledger event, chained under the
      code action's seq (a real nested causal DAG, not a flat list);
  (3) namespace STATE PERSISTS across two code actions in one session (a var/def
      set in action #1 is still defined in action #2 — the "memory" guarantee);
  (4) a runaway (infinite-loop) code action is KILLED by wall-time fuel WITHOUT
      hanging the loop, the event records success=False, and the kernel respawns
      so the NEXT action runs cleanly;
  (5) the resulting on-disk journal passes korg_ledger.verify_journal_file (HMAC
      hash-chain + DAG both intact) AND ledger_trace.render_trace shows the python
      action with its nested sub-calls indented underneath.

This caught a genuine production bug: KernelHandle._spawn set cwd=<workspace> but
never put the korgex install root on the child's PYTHONPATH, so `python -m
src.codeact.kernel_main` died at import ("exited before READY") for every
workspace that wasn't the korgex checkout itself (i.e. every worktree + every
tmp). Unit tests that imported execute_code in-process never hit it; only driving
the loop through a real subprocess rooted at a tmp dir does. That is the whole
point of a loop-level safety net.

Runs fully OFFLINE: the model is a scripted list of responses; the kernel, the
bridge, the gate stack, the ledger, route_tool_call, and the trace renderer are
all the REAL code paths.
"""

import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from src.agent import KorgexAgent  # noqa: E402
from src import korg_ledger as L  # noqa: E402
from src import ledger_trace as T  # noqa: E402


# ── scripted-model harness (offline; the validated test_codeact_loop precedent) ─

def _openai_text(text):
    """A no-tool assistant turn — terminates the loop."""
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text, tool_calls=None))],
    )


def _python_call(call_id, code):
    """An assistant turn emitting a single tool_use name='python' input={'code':…}.

    Shaped like an OpenAI ChatCompletion so the REAL _extract_tool_calls /
    _assistant_turn normalize it (the contract: both vendors collapse to {id,name,args})."""
    msg = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(
                name="python", arguments=json.dumps({"code": code})),
        )],
    )
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=msg)])


class _ScriptedAgent(KorgexAgent):
    """KorgexAgent whose provider is a fixed list of responses popped in order.

    EVERYTHING else — _dispatch_call → _run_code_action → KernelHandle.exec →
    the NDJSON wire → _bridge_tool_call → the gate stack → route_tool_call → the
    ledger — is the real, unmocked code path. The ledger is a real on-disk
    LocalJournalClient so verify_journal_file runs against true bytes."""

    def __init__(self, responses, *, journal_path, repo_root):
        super().__init__(model="gpt-4o", interactive=False, repo_root=repo_root)
        self._responses = list(responses)
        self.ledger = L.LocalJournalClient(journal_path=journal_path)

    def _get_client(self):
        return object()  # never used — _call is stubbed below

    def _call(self, client, messages, tools, output_schema=None,
              system_prompt=None, system_volatile=None):
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _offline_local_ledger(monkeypatch):
    # Pin the durable ladder to a local file (no bridge / HTTP) and force the
    # compression threshold low so large-result behavior is deterministic.
    monkeypatch.setenv("KORGEX_LEDGER", "local")
    monkeypatch.setenv("KORGEX_COMPRESS_THRESHOLD", "256")
    yield


def _drive(agent, prompt="do the work"):
    """Run one full task to completion and ALWAYS reap the kernel (no leaked child)."""
    try:
        return agent.run_task(prompt)
    finally:
        k = getattr(agent, "_kernel", None)
        if k is not None:
            k.reset()


def _events(jpath):
    """Raw on-disk events (load_journal_raw, NOT recall — keeps entry_hash so the
    chain verifies; the historical 'verify crash on array journal' bug lived here)."""
    return L.load_journal_raw(str(jpath))


# ════════════════════════════════════════════════════════════════════════════
# (1) a code action runs in the kernel and its result returns to the loop
# ════════════════════════════════════════════════════════════════════════════

def test_code_action_runs_in_kernel_and_result_returns_to_loop(tmp_path):
    jpath = tmp_path / "journal.jsonl"
    # A pure-compute action: print + a trailing bare expression (captured as result).
    code = "vals = [i*i for i in range(4)]\nprint('SQUARES', vals)\nsum(vals)"
    agent = _ScriptedAgent(
        [_python_call("c1", code), _openai_text("done")],
        journal_path=str(jpath), repo_root=str(tmp_path),
    )
    result = _drive(agent)
    assert result["success"] is True  # the loop ran to completion

    raw = _events(jpath)
    py = [e for e in raw if e["tool_name"] == "python"]
    assert len(py) == 1, "the loop must record exactly one 'python' tool event"
    res = py[0]["result"]
    # The kernel's exec_result body came back UP through the loop intact: stdout
    # captured, the last bare expression (0+1+4+9 = 14) returned as `result`.
    assert res.get("ok") is True
    assert "SQUARES [0, 1, 4, 9]" in res.get("stdout", "")
    assert res.get("result") == 14
    # No bridged tool fired (pure compute) — the wire serviced zero tool_call RPCs.
    assert not [e for e in raw if e["tool_name"] in ("Read", "Bash", "Grep")]


def test_tools_filter_is_enforced_inside_a_code_action(tmp_path):
    # REGRESSION (adversarial verify, security): a restricted subagent (e.g. a
    # read-only explore agent) could escalate to write/bash by routing through a
    # python action — _bridge_tool_call ignored tools_filter. It must enforce the
    # SAME allowlist the serial loop does, so the in-code tools obey it too.
    jpath = tmp_path / "journal.jsonl"
    agent = _ScriptedAgent([], journal_path=str(jpath), repo_root=str(tmp_path))
    target = tmp_path / "should_not_exist.txt"
    denied = agent._bridge_tool_call(
        "Write", {"file_path": str(target), "content": "x"},
        code_action_seq=1, tools_filter={"Read", "Grep"})
    assert "error" in denied and "not permitted" in denied["error"]
    assert not target.exists()                      # the write never executed
    # a deny is recorded so the trace shows the refusal, chained under the action
    raw = _events(jpath)
    assert any(e["tool_name"] == "tools_filter.deny" for e in raw)
    # a tool that IS on the allowlist is not refused by the filter (read a fresh
    # file — NOT the journal, whose content now contains the deny event's text)
    ok_file = tmp_path / "ok.txt"
    ok_file.write_text("hello")
    allowed = agent._bridge_tool_call(
        "Read", {"file_path": str(ok_file)}, code_action_seq=1,
        tools_filter={"Read", "Grep"})
    assert allowed.get("content") == "hello"


def test_bridged_results_are_raw_not_compressed(tmp_path):
    # REGRESSION (wire dogfood): _bridge_tool_call ran _compress_tool_result on the
    # value handed BACK INTO the kernel, so a large read_file(p) returned a
    # {_ref, view} stub with no 'content' → read_file(p)['content'] raised KeyError
    # and CodeAct was unusable. Code must receive the RAW result; compression is only
    # for the model's CONTEXT. (The autouse fixture pins the compress threshold to
    # 256 bytes, so this file is well over it.)
    jpath = tmp_path / "journal.jsonl"
    agent = _ScriptedAgent([], journal_path=str(jpath), repo_root=str(tmp_path))
    big = tmp_path / "big.py"
    big.write_text("def f():\n    pass\n" * 5000)  # ~90KB, far over the 256B threshold
    out = agent._bridge_tool_call("Read", {"file_path": str(big)}, code_action_seq=1)
    assert "_compressed" not in out               # NOT a compressed stub
    assert out.get("content", "").count("def ") == 5000  # code can compute on real data


# ════════════════════════════════════════════════════════════════════════════
# (2) bridged tools route through route_tool_call; EACH sub-call is a ledger
#     event chained UNDER the code-action seq (a real nested DAG)
# ════════════════════════════════════════════════════════════════════════════

def test_bridged_subcalls_route_and_chain_under_the_code_action(tmp_path):
    (tmp_path / "hi.txt").write_text("alpha\nbeta\nalpha\n")
    jpath = tmp_path / "journal.jsonl"
    # ONE action composing THREE governed tools — the CodeAct thesis (compose, not
    # one-call-per-turn). read_file + bash + grep each round-trip the wire.
    code = (
        "f = read_file('hi.txt')\n"
        "b = bash('echo wired')\n"
        "g = grep('alpha', 'hi.txt')\n"
        "print('SIZE', f.get('size'))\n"
        "print('ECHO', b.get('stdout', '').strip())\n"
        "{'fkeys': sorted(f.keys()), 'echo': b.get('stdout','').strip(), "
        " 'rc': b.get('exit_code'), 'gkeys': sorted(g.keys())}"
    )
    agent = _ScriptedAgent(
        [_python_call("c1", code), _openai_text("done")],
        journal_path=str(jpath), repo_root=str(tmp_path),
    )
    result = _drive(agent)
    assert result["success"] is True

    raw = _events(jpath)
    # The dual-event shape: the loop's "python" tool event AND the
    # record_user_prompt anchor, BOTH under the same llm_inference (siblings).
    llm = [e for e in raw if e["tool_name"] == "llm_inference"]
    llm_seqs = {e["seq_id"] for e in llm}
    py = [e for e in raw if e["tool_name"] == "python"]
    assert len(py) == 1
    assert py[0]["triggered_by"] in llm_seqs

    anchor = [e for e in raw if e["tool_name"] == "user_prompt"
              and str((e.get("args") or {}).get("prompt", "")).startswith("[python action]")]
    assert len(anchor) == 1, "the code action must have a seq-returning anchor"
    code_seq = anchor[0]["seq_id"]
    assert anchor[0]["triggered_by"] in llm_seqs

    # EACH bridged sub-call is its own event, chained UNDER code_seq (not the llm,
    # not each other) — this is the edge that makes the in-code call part of the
    # causal DAG and keeps `why <file>` attributable (the sub-call carries the
    # real path/command; the opaque python action does not).
    subs = [e for e in raw if e["tool_name"] in ("Read", "Bash", "Grep")]
    names = sorted(e["tool_name"] for e in subs)
    assert names == ["Bash", "Grep", "Read"], f"expected all 3 sub-calls, got {names}"
    assert all(e["triggered_by"] == code_seq for e in subs), \
        "every bridged sub-call must chain under the code-action seq"
    # The Read sub-call carries the REAL file_path (attributability), not the code blob.
    rd = [e for e in subs if e["tool_name"] == "Read"][0]
    assert rd["args"].get("file_path") == "hi.txt"
    # And it actually went through the governed router (real result, success=True).
    assert rd["success"] is True
    # The trailing expression — built from ALL THREE bridged results inside the
    # kernel — round-tripped back to the loop as structured JSON, proving each
    # tool's real dict came back over the wire (not a stub): Read→{content,...},
    # Bash→{stdout,exit_code,...}, Grep→{matches,total}.
    res = py[0]["result"]["result"]
    assert res["echo"] == "wired"
    assert res["rc"] == 0
    assert "content" in res["fkeys"] and "size" in res["fkeys"]
    assert "matches" in res["gkeys"] and "total" in res["gkeys"]


# ════════════════════════════════════════════════════════════════════════════
# (3) namespace STATE PERSISTS across two code actions in one session
# ════════════════════════════════════════════════════════════════════════════

def test_state_persists_across_two_code_actions_in_one_session(tmp_path):
    jpath = tmp_path / "journal.jsonl"
    # Action #1 defines a var AND a function. Action #2 uses BOTH — only possible
    # if the kernel reused the SAME persistent GLOBALS (the "code as action space
    # WITH memory" guarantee). A fresh namespace would NameError here.
    code1 = "counter = 40\ndef bump(n):\n    return n + 2\nprint('SET', counter)"
    code2 = "counter = bump(counter)\nprint('AFTER', counter)\ncounter"
    agent = _ScriptedAgent(
        [_python_call("c1", code1), _python_call("c2", code2), _openai_text("done")],
        journal_path=str(jpath), repo_root=str(tmp_path),
    )
    result = _drive(agent)
    assert result["success"] is True

    raw = _events(jpath)
    py = [e for e in raw if e["tool_name"] == "python"]
    assert len(py) == 2
    # Both actions ran cleanly (no NameError on the carried-over var/def).
    assert all(e["result"].get("ok") is True for e in py), \
        f"a python action errored: {[e['result'] for e in py]}"
    assert "SET 40" in py[0]["result"]["stdout"]
    # The SECOND action saw counter==40 from #1 AND called bump() defined in #1.
    assert "AFTER 42" in py[1]["result"]["stdout"]
    assert py[1]["result"]["result"] == 42


# ════════════════════════════════════════════════════════════════════════════
# (4) a runaway code action is KILLED by fuel without hanging the loop; the
#     kernel respawns and the NEXT action runs
# ════════════════════════════════════════════════════════════════════════════

def test_runaway_action_is_fuel_killed_loop_survives_and_kernel_respawns(
        tmp_path, monkeypatch):
    # Tight wall fuel so the infinite loop is killed fast. If the parent's
    # deadline guard were missing, this test would HANG forever — that is the
    # regression it exists to catch (kill-on-deadline, not an in-kernel alarm).
    monkeypatch.setenv("KORGEX_CODEACT_FUEL_MS", "800")
    jpath = tmp_path / "journal.jsonl"
    runaway = "while True:\n    pass"          # never returns
    recover = "print('ALIVE AGAIN')\n6 * 7"    # must run on a respawned kernel
    agent = _ScriptedAgent(
        [_python_call("c1", runaway), _python_call("c2", recover), _openai_text("done")],
        journal_path=str(jpath), repo_root=str(tmp_path),
    )
    result = _drive(agent)
    # The loop COMPLETED (it did not hang on the runaway) and returned normally.
    assert result["success"] is True

    raw = _events(jpath)
    py = [e for e in raw if e["tool_name"] == "python"]
    assert len(py) == 2
    # #1 was fuel-killed → recorded success=False with a timeout/reset error dict.
    assert py[0]["success"] is False
    err = json.dumps(py[0]["result"]).lower()
    assert "timed out" in err and "reset" in err
    # #2 respawned a fresh kernel and ran to completion (last-expr 42). That #2
    # produced a real exec_result AT ALL is the proof the kernel respawned after
    # the kill — a wedged/None handle could not have run it. (We don't assert
    # _kernel.alive here: _drive's finally reaps the child before this body runs.)
    assert py[1]["success"] is True
    assert py[1]["result"].get("result") == 42
    assert "ALIVE AGAIN" in py[1]["result"]["stdout"]


# ════════════════════════════════════════════════════════════════════════════
# (5) the journal verifies (chain + DAG) AND the trace renders the code action
#     with its nested sub-calls
# ════════════════════════════════════════════════════════════════════════════

def test_journal_verifies_and_trace_shows_nested_code_action(tmp_path):
    (tmp_path / "doc.txt").write_text("findme\n")
    jpath = tmp_path / "journal.jsonl"
    code = "f = read_file('doc.txt')\ng = grep('findme', 'doc.txt')\nprint('OK')"
    agent = _ScriptedAgent(
        [_python_call("c1", code), _openai_text("done")],
        journal_path=str(jpath), repo_root=str(tmp_path),
    )
    _drive(agent)

    # ── chain + DAG both intact on disk (one call proves both). The historical
    #    'verify crash on array journal' + DAG-edge-direction bugs surface here.
    assert L.verify_journal_file(str(jpath)) == []

    raw = _events(jpath)
    # Sanity: the nested structure that the trace will render actually exists.
    anchor = [e for e in raw if e["tool_name"] == "user_prompt"
              and str((e.get("args") or {}).get("prompt", "")).startswith("[python action]")][0]
    code_seq = anchor["seq_id"]
    subs = [e for e in raw if e["tool_name"] in ("Read", "Grep")]
    assert subs and all(e["triggered_by"] == code_seq for e in subs)

    # ── the trace renderer shows the code action with its sub-calls INDENTED
    #    underneath the anchor — explainable cognition over the verified chain.
    txt = T.render_trace(raw, color=False)
    lines = txt.splitlines()

    def _indent(substr):
        for ln in lines:
            if substr in ln:
                return len(ln) - len(ln.lstrip(" "))
        raise AssertionError(f"trace missing a line containing {substr!r}:\n{txt}")

    anchor_indent = _indent("[python action]")
    read_indent = _indent("Read doc.txt")
    grep_indent = _indent("Grep")
    # Each sub-call is rendered MORE indented than the code-action anchor → nested.
    assert read_indent > anchor_indent, f"Read not nested under the action:\n{txt}"
    assert grep_indent > anchor_indent, f"Grep not nested under the action:\n{txt}"
    # The opaque "python" tool event is rendered too (the dual-event shape), as a
    # sibling of the anchor (same indent — both hang off the llm_inference).
    py_indent = _indent("✓ python")
    assert py_indent == anchor_indent
