"""ACP mid-turn session/cancel — an editor's "stop" actually interrupts a turn.

A single-threaded stdio loop can't read the cancel while a turn runs, so serve()
reads on a background thread that flips the session's cancel flag the instant a
cancel arrives; the agent loop checks a `should_cancel` callback between rounds and
stops cleanly. These pin all three layers: the agent stop, the bridge wiring, and
the threaded transport.
"""
import io
import json
import queue
import threading

from src import acp


def _req(mid, method, params=None):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}


class _Led:
    def __init__(self):
        self.events = []

    def record_user_prompt(self, p, triggered_by=None):
        return 1

    def record_llm_call(self, **k):
        return 2

    def record_tool_call(self, **k):
        self.events.append(k)
        return 3


# ── agent side: the loop returns a cancelled result when should_cancel fires ─────

def test_run_task_stops_when_should_cancel_fires(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    a = KorgexAgent(model="gpt-4o", interactive=False, repo_root=str(tmp_path), ledger=_Led())
    monkeypatch.setattr(a, "_get_client", lambda: object())
    a._should_cancel = lambda: True          # cancel before the first round does any work
    res = a.run_task("do something long")
    assert res.get("cancelled") is True
    assert res["result"] == "(cancelled)"


def test_run_task_runs_normally_when_not_cancelled(tmp_path, monkeypatch):
    # Sanity: with should_cancel False, the loop proceeds as usual (finishes a turn).
    from src.agent import KorgexAgent
    a = KorgexAgent(model="gpt-4o", interactive=False, repo_root=str(tmp_path), ledger=_Led())
    monkeypatch.setattr(a, "_get_client", lambda: object())
    a._should_cancel = lambda: False

    def fake_call(client, messages, tools_payload, system_prompt=None, system_volatile=None):
        class R:
            pass
        r = R()
        r._text = "all done"
        r.usage = None
        r._calls = []
        return r
    monkeypatch.setattr(a, "_call", fake_call)
    monkeypatch.setattr(a, "_extract_tool_calls", lambda r: r._calls)
    monkeypatch.setattr(a, "_extract_final_text", lambda r: r._text)
    res = a.run_task("hi")
    assert not res.get("cancelled")
    assert res.get("success") is True


# ── bridge: make_live_run_turn connects should_cancel to the session flag ────────

def test_bridge_wires_should_cancel_to_session_flag():
    from src.plugins import PluginRegistry
    seen = {}

    class _StubAgent:
        def __init__(self):
            self.plugins = PluginRegistry()
            self.repo_root = None
            self._should_cancel = None

        def run_task(self, prompt):
            seen["fn"] = self._should_cancel
            return {"success": True, "result": "done"}

    rt = acp.make_live_run_turn(lambda: _StubAgent())
    a = acp.AcpAgent(run_turn=rt, send=lambda m: None)
    sid = a.handle(_req(1, "session/new", {}))["result"]["sessionId"]
    a.handle(_req(2, "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "go"}]}))
    fn = seen["fn"]
    assert callable(fn) and fn() is False         # not cancelled yet
    a.sessions[sid]["cancelled"] = True
    assert fn() is True                            # flips with the session flag


# ── transport: the reader thread flips the flag while a turn is in flight ────────

class _BlockingIn:
    """An instream whose readline() blocks until the test feeds a line (None = EOF)."""
    def __init__(self):
        self.q: queue.Queue = queue.Queue()

    def feed(self, line):
        self.q.put(line)

    def readline(self):
        item = self.q.get()
        return "" if item is None else item


def test_serve_cancel_arrives_mid_turn_via_reader_thread():
    instream = _BlockingIn()
    out = io.StringIO()
    started = threading.Event()
    release = threading.Event()
    cancel_done = threading.Event()
    saw = {}

    def run_turn(text, session):
        started.set()                 # the turn is now "running"
        release.wait(timeout=3)       # ...held until the test injects a cancel
        saw["cancelled"] = bool(session.get("cancelled"))
        return {"text": "", "stop_reason": "cancelled" if session.get("cancelled") else "end_turn"}

    agent = acp.AcpAgent(run_turn=run_turn)
    # signal when the reader thread has actually processed the cancel
    orig_cancel = agent._session_cancel
    def hooked(params):
        orig_cancel(params)
        cancel_done.set()
    agent._session_cancel = hooked

    t = threading.Thread(target=acp.serve, kwargs={"agent": agent, "instream": instream, "outstream": out},
                         daemon=True)
    t.start()
    instream.feed(json.dumps(_req(1, "session/new", {})) + "\n")
    instream.feed(json.dumps(_req(2, "session/prompt",
                                  {"sessionId": "korgex-1",
                                   "prompt": [{"type": "text", "text": "go"}]})) + "\n")
    assert started.wait(timeout=3), "the turn should have started"
    # inject the cancel WHILE the turn is blocked — only the reader thread can read it
    instream.feed(json.dumps({"jsonrpc": "2.0", "method": "session/cancel",
                              "params": {"sessionId": "korgex-1"}}) + "\n")
    assert cancel_done.wait(timeout=3), "reader thread should have handled the cancel"
    release.set()                     # let the (now-cancelled) turn finish
    instream.feed(None)               # EOF → serve loop exits
    t.join(timeout=3)

    assert saw["cancelled"] is True   # the in-flight turn observed the cancel
    # and the protocol layer reported a cancelled stop reason
    msgs = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    prompt_resp = next(m for m in msgs if m.get("id") == 2)
    assert prompt_resp["result"]["stopReason"] == "cancelled"
