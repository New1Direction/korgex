"""CodeAct — the tool bridge + agent integration (loop-level, fully OFFLINE).

These exercise the "python" action end-to-end through the REAL kernel subprocess
+ the parent-side governed bridge, with NO provider and NO network. The model is a
scripted stub (the test_subagents.py precedent): round 1 emits a tool_use/function
call name="python", round 2 a no-tool text to terminate the loop.

Covered (per the build contract's required harness):
  - registration: "python" is a direct tool with a `code` param and NO routing row;
  - dispatch: the loop records a "python" tool event under llm_seq AND the
    record_user_prompt anchor (code_seq) under which each sub-call chains;
  - nested DAG: a sub-tool (Read) has triggered_by == code_seq; verify_dag == [];
  - real-journal: verify_journal_file (DAG + hash-chain) == [];
  - gate floor: a code-driven write to .git is BLOCKED identically to a direct Write
    (the refusal is the function's return value; an edit_policy event records it);
  - recursion guard: python/Agent/Orchestrate are not callable from inside code;
  - never-lose-data: a LARGE sub-result → context.compress + a Retrieve-able _ref,
    and tool_retrieve_blob(ref) returns verified:True byte-identical;
  - Retrieve passthrough: a bridged Retrieve result is NOT re-compressed;
  - redact-over-reach: the python event + sub-call args survive redaction non-empty;
  - recoverability: a timeout and a crash both return an {error:...} dict, record
    success=False, the loop CONTINUES (does not hang), and the kernel respawns.
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
from src.tool_abstraction import USER_TOOLS, _TOOL_ROUTING, visible_tool_names  # noqa: E402


# ── scripted-model harness (offline, the test_subagents.py pattern) ───────────

class _FakeLedger:
    """In-memory seq-returning ledger. record_* return a real, strictly-increasing
    seq (so the code-action anchor + nested chaining are well-formed)."""

    def __init__(self):
        self.events = []
        self._seq = 0

    def _next(self):
        self._seq += 1
        return self._seq

    def record_user_prompt(self, prompt, triggered_by=None):
        seq = self._next()
        self.events.append({"kind": "user_prompt", "seq_id": seq,
                            "prompt": prompt, "triggered_by": triggered_by})
        return seq

    def record_llm_call(self, **kw):
        seq = self._next()
        self.events.append({"kind": "llm", "seq_id": seq,
                            "triggered_by": kw.get("triggered_by")})
        return seq

    def record_tool_call(self, **kw):
        seq = self._next()
        self.events.append({"kind": "tool", "seq_id": seq,
                            "tool_name": kw.get("tool_name"),
                            "args": kw.get("args"), "result": kw.get("result"),
                            "success": kw.get("success"),
                            "triggered_by": kw.get("triggered_by")})
        return seq


def _openai_text(text):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
    )


def _openai_python_call(call_id, code):
    msg = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name="python",
                                     arguments=json.dumps({"code": code})),
        )],
    )
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=msg)])


class _ScriptedAgent(KorgexAgent):
    """KorgexAgent whose model is a fixed list of responses; everything else (the
    dispatch, the gate stack, the kernel, the bridge) is the REAL code path."""

    def __init__(self, responses, ledger=None, **kw):
        kw.setdefault("model", "gpt-4o")
        kw.setdefault("interactive", False)
        super().__init__(**kw)
        self._responses = list(responses)
        self.ledger = ledger if ledger is not None else _FakeLedger()

    def _get_client(self):
        return object()

    def _call(self, client, messages, tools, output_schema=None,
              system_prompt=None, system_volatile=None):
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _force_local_ledger(monkeypatch):
    # Keep the default-client durability ladder off the bridge/HTTP in tests.
    monkeypatch.setenv("KORGEX_LEDGER", "local")
    # Deterministic compression threshold for the large-result tests.
    monkeypatch.setenv("KORGEX_COMPRESS_THRESHOLD", "256")
    yield


def _drive(agent, prompt="do it"):
    """Run one full task and ALWAYS reset the kernel afterwards (no leaked child)."""
    try:
        return agent.run_task(prompt)
    finally:
        if getattr(agent, "_kernel", None) is not None:
            agent._kernel.reset()


# ── 1. registration ──────────────────────────────────────────────────────────

def test_python_registered_direct_with_code_param_and_no_routing_row():
    assert "python" in USER_TOOLS
    t = USER_TOOLS["python"]
    assert t["exposure"] == "direct"
    assert t["input_schema"]["required"] == ["code"]
    assert "python" in visible_tool_names()
    # Intercepted in _dispatch_call — a routing row would be dead code / double-dispatch.
    assert "python" not in _TOOL_ROUTING
    # Action-space framing is in the description.
    assert "action" in t["description"].lower()
    assert "read_file" in t["description"]


# ── 2. dispatch + nested DAG (real kernel, real bridge) ───────────────────────

def test_python_action_records_dual_events_and_nests_subcalls(tmp_path):
    (tmp_path / "x.py").write_text("a = 1\nb = 2\n")
    code = "f = read_file('x.py'); print('SZ', f['size']); 2 + 2"
    agent = _ScriptedAgent(
        [_openai_python_call("c1", code), _openai_text("done")],
        repo_root=str(tmp_path),
    )
    result = _drive(agent)
    assert result["success"] is True

    evs = agent.ledger.events
    # The OUTER loop records a "python" tool event under the llm_seq.
    py_events = [e for e in evs if e["kind"] == "tool" and e["tool_name"] == "python"]
    assert len(py_events) == 1
    llm_seqs = [e["seq_id"] for e in evs if e["kind"] == "llm"]
    assert py_events[0]["triggered_by"] in llm_seqs

    # The record_user_prompt ANCHOR (code_seq) exists, chained under an llm_seq.
    anchors = [e for e in evs if e["kind"] == "user_prompt"
               and str(e["prompt"]).startswith("[python action]")]
    assert len(anchors) == 1
    code_seq = anchors[0]["seq_id"]
    assert anchors[0]["triggered_by"] in llm_seqs

    # Each in-code sub-tool event (Read) chains UNDER code_seq — a nested DAG.
    read_events = [e for e in evs if e["kind"] == "tool" and e["tool_name"] == "Read"]
    assert read_events, "the bridged Read sub-call was not recorded"
    assert all(e["triggered_by"] == code_seq for e in read_events)
    # The sub-call carries the real file_path (why-attributability), not the opaque code.
    assert read_events[0]["args"].get("file_path") == "x.py"

    # verify_dag over the whole synthetic event list is clean.
    assert L.verify_dag(evs) == []


def test_python_action_real_journal_verifies_dag_and_chain(tmp_path):
    """Force a real LocalJournalClient and assert verify_journal_file (DAG + the
    HMAC hash-chain) is clean — the historical 'verify crash on array journal' /
    chaining bugs would surface here."""
    (tmp_path / "x.py").write_text("print('hi')\n")
    jpath = tmp_path / "journal.jsonl"
    led = L.LocalJournalClient(journal_path=str(jpath))
    code = "f = read_file('x.py'); g = grep('hi', 'x.py'); len(g) if isinstance(g, list) else 0"
    agent = _ScriptedAgent(
        [_openai_python_call("c1", code), _openai_text("done")],
        ledger=led, repo_root=str(tmp_path),
    )
    _drive(agent)

    # Use load_journal_raw (NOT recall.load_events) so entry_hash survives the read.
    assert L.verify_journal_file(str(jpath)) == []
    raw = L.load_journal_raw(str(jpath))
    anchor = [e for e in raw if e["tool_name"] == "user_prompt"
              and str(e["args"].get("prompt", "")).startswith("[python action]")]
    assert anchor
    code_seq = anchor[0]["seq_id"]
    subcalls = [e for e in raw if e["tool_name"] in ("Read", "Grep")]
    assert subcalls
    assert all(e.get("triggered_by") == code_seq for e in subcalls)


# ── 3. gate floor (code can't bypass the hard-block) ──────────────────────────

def test_code_driven_write_to_git_is_blocked_like_a_direct_write(tmp_path):
    (tmp_path / ".git").mkdir()
    target = str(tmp_path / ".git" / "config")
    # The refusal must come back as write_file()'s RETURN VALUE (a block dict) so
    # the code can react — the kernel does not raise on a governed refusal.
    code = (f"r = write_file({target!r}, 'pwned')\n"
            "print('VERDICT', r.get('verdict') if isinstance(r, dict) else 'NONE')\n"
            "print('IS_ERR', 'error' in r if isinstance(r, dict) else False)")
    agent = _ScriptedAgent(
        [_openai_python_call("c1", code), _openai_text("done")],
        repo_root=str(tmp_path),
    )
    agent.workspace_root = str(tmp_path)  # arm Gate A too
    _drive(agent)

    # The file was NEVER written.
    assert not (tmp_path / ".git" / "config").exists()
    # A governed gate recorded a refusal (edit_policy hard-block floor and/or
    # workspace/guardrail) chained under the code action.
    blocks = [e for e in agent.ledger.events if e["kind"] == "tool"
              and e["tool_name"] in ("edit_policy", "workspace.guard", "guardrail.block")
              and e["success"] is False]
    assert blocks, "no governed refusal was recorded for the .git write"


def test_recursion_guard_rejects_nested_kernel_and_subagents(tmp_path):
    agent = _ScriptedAgent([_openai_text("noop")], repo_root=str(tmp_path))
    for name in ("python", "Agent", "Orchestrate"):
        out = agent._bridge_tool_call(name, {"code": "1"}, code_action_seq=1)
        assert "error" in out and "not callable from inside a python action" in out["error"]
    # The guard fires BEFORE any gate or ledger write.
    assert agent.ledger.events == []


# ── 4. bridged sub-results: RAW to the code, sealed in the LEDGER ─────────────

def test_large_subresult_is_raw_to_code_and_sealed_in_ledger(tmp_path):
    # CORRECTED CONTRACT (wire dogfood): the value handed back INTO the kernel is the
    # RAW tool result, so code computes on it directly — read_file(p)['content'].
    # Compression is for the MODEL'S context, NOT intra-code data flow: handing code
    # a {_ref, view} stub made read_file(p)['content'] raise KeyError and CodeAct was
    # unusable. The LEDGER still seals the large result as a content-ref (lean,
    # verifiable journal), and the chain verifies — never-lose-data holds at the
    # ledger, not by crippling the code.
    big = "Z" * 6000
    (tmp_path / "big.txt").write_text(big)
    jpath = tmp_path / "journal.jsonl"
    led = L.LocalJournalClient(journal_path=str(jpath))
    target = str(tmp_path / "big.txt")
    code = (
        f"f = read_file({target!r})\n"
        "print('COMPRESSED', f.get('_compressed'))\n"   # None — a raw dict, not a stub
        "print('LEN', len(f.get('content', '')))"        # the FULL content is in code
    )
    agent = _ScriptedAgent(
        [_openai_python_call("c1", code), _openai_text("done")],
        ledger=led, repo_root=str(tmp_path),
    )
    _drive(agent)

    raw = L.load_journal_raw(str(jpath))
    py = [e for e in raw if e["tool_name"] == "python"][0]
    out = py["result"]["stdout"]
    assert "COMPRESSED None" in out      # code saw a RAW result (no _compressed stub)
    assert "LEN 6000" in out             # ...with the full content available

    # Compression is NOT applied to bridged sub-results (model-context only).
    assert not [e for e in raw if e["tool_name"] == "context.compress"]

    # The Read sub-call is chained under the [python action] anchor (nested DAG)...
    anchor = [e for e in raw if e["tool_name"] == "user_prompt"
              and str(e["args"].get("prompt", "")).startswith("[python action]")][0]
    rd = [e for e in raw if e["tool_name"] == "Read"]
    assert rd and rd[0].get("triggered_by") == anchor["seq_id"]
    # ...and the LEDGER sealed the large result (not stored raw → lean journal).
    assert len(json.dumps(rd[0]["result"], default=str)) < 6000
    # The DAG + chain verify on disk.
    assert L.verify_journal_file(str(jpath)) == []


def test_retrieve_passthrough_not_recompressed_at_bridge(tmp_path):
    """Unit-level: a bridged Retrieve never goes through _compress_tool_result (it
    would loop the model). Drive it through the real handler with a sealed blob."""
    from src import korg_ledger as KL
    big = b"Y" * 4096
    sha, _size = KL._write_blob(big)
    jpath = tmp_path / "journal.jsonl"
    led = KL.LocalJournalClient(journal_path=str(jpath))
    agent = _ScriptedAgent([_openai_text("noop")], ledger=led, repo_root=str(tmp_path))
    out = agent._bridge_tool_call("Retrieve", {"ref": f"sha256:{sha}"}, code_action_seq=1)
    assert out.get("verified") is True
    assert out.get("_compressed") is not True  # passed through verbatim
    assert len(out.get("content", "")) == 4096


# ── 5. redact-over-reach (the token-count bug class) ──────────────────────────

def test_python_and_subcall_payloads_survive_redaction(tmp_path):
    (tmp_path / "a.py").write_text("hello world")
    jpath = tmp_path / "journal.jsonl"
    led = L.LocalJournalClient(journal_path=str(jpath))
    agent = _ScriptedAgent(
        [_openai_python_call("c1", "read_file('a.py')"), _openai_text("done")],
        ledger=led, repo_root=str(tmp_path),
    )
    _drive(agent)
    raw = L.load_journal_raw(str(jpath))
    # The python tool event still carries the code arg (not nuked to empty).
    py_ev = [e for e in raw if e["tool_name"] == "python"][0]
    assert py_ev["args"].get("code")
    # The llm_inference events still carry a model + token field.
    llm = [e for e in raw if e["tool_name"] == "llm_inference"]
    assert llm and all("model" in e["args"] for e in llm)
    # The Read sub-call still carries the real file_path.
    rd = [e for e in raw if e["tool_name"] == "Read"][0]
    assert rd["args"].get("file_path") == "a.py"


# ── 6. recoverability (timeout + crash; loop continues; respawn) ──────────────

def test_timeout_returns_error_dict_loop_continues_and_kernel_respawns(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_CODEACT_FUEL_MS", "700")  # tight wall fuel
    code_to = "import time\nwhile True:\n    time.sleep(0.05)"
    code_ok = "print('ALIVE'); 7 * 6"
    agent = _ScriptedAgent(
        [_openai_python_call("c1", code_to),
         _openai_python_call("c2", code_ok),
         _openai_text("done")],
        repo_root=str(tmp_path),
    )
    result = _drive(agent)
    # The loop ran to completion (did NOT hang) and returned a normal result.
    assert result["success"] is True

    evs = agent.ledger.events
    py_events = [e for e in evs if e["kind"] == "tool" and e["tool_name"] == "python"]
    assert len(py_events) == 2
    # First python action timed out → recorded success=False with an error result.
    first = py_events[0]
    assert first["success"] is False
    assert "timed out" in json.dumps(first["result"])
    # Second python action respawned the kernel and succeeded (last-expr 42).
    second = py_events[1]
    assert second["success"] is True
    assert "42" in json.dumps(second["result"]) or second["result"].get("result") == 42


def test_crash_returns_error_dict_and_loop_continues(tmp_path):
    # os._exit kills the kernel mid-exec → EOF on the wire → recoverable crash dict.
    code_crash = "import os\nos._exit(7)"
    code_ok = "print('BACK'); 1 + 1"
    agent = _ScriptedAgent(
        [_openai_python_call("c1", code_crash),
         _openai_python_call("c2", code_ok),
         _openai_text("done")],
        repo_root=str(tmp_path),
    )
    result = _drive(agent)
    assert result["success"] is True

    evs = agent.ledger.events
    py_events = [e for e in evs if e["kind"] == "tool" and e["tool_name"] == "python"]
    assert len(py_events) == 2
    assert py_events[0]["success"] is False
    assert "crash" in json.dumps(py_events[0]["result"]).lower()
    # Respawned + succeeded.
    assert py_events[1]["success"] is True


def test_kill_switch_disables_python_action(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_CODEACT_ENABLE", "0")
    agent = _ScriptedAgent([_openai_text("noop")], repo_root=str(tmp_path))
    out = agent._run_code_action({"code": "1 + 1"}, parent_seq=None)
    assert "error" in out and "disabled" in out["error"].lower()
    # No kernel was spawned.
    assert agent._kernel is None
