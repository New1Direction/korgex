"""ACP (Agent Client Protocol) — korgex as an ACP *agent*: JSON-RPC 2.0 over stdio so
editors/clients (Zed et al.) can drive it. Implemented clean-room from the open spec
(agentclientprotocol.com): client→agent initialize / session.new / session.prompt /
session.cancel; agent→client session/update notifications (discriminator `sessionUpdate`,
content blocks keyed by `type`); stop reasons end_turn|max_tokens|cancelled|refusal.

The protocol layer is unit-tested here; end-to-end against a real ACP client is the
remaining validation (noted in src/acp.py).
"""
import io
import json

from src import acp


def _req(mid, method, params=None):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}


def test_initialize_returns_capabilities():
    resp = acp.AcpAgent().handle(_req(1, "initialize", {
        "protocolVersion": 1, "clientCapabilities": {"fs": {"readTextFile": True}}}))
    assert resp["id"] == 1
    r = resp["result"]
    assert r["protocolVersion"] == 1
    assert "agentCapabilities" in r and "authMethods" in r


def test_session_new_returns_a_session_id_and_tracks_it():
    a = acp.AcpAgent()
    sid = a.handle(_req(2, "session/new", {"cwd": "/repo", "mcpServers": []}))["result"]["sessionId"]
    assert sid and sid in a.sessions


def test_session_prompt_streams_a_chunk_and_returns_stop_reason():
    sent = []
    a = acp.AcpAgent(run_turn=lambda text, sess: {"text": f"did: {text}", "stop_reason": "end_turn"},
                     send=sent.append)
    sid = a.handle(_req(1, "session/new", {"cwd": "/r"}))["result"]["sessionId"]
    resp = a.handle(_req(2, "session/prompt",
                         {"sessionId": sid, "prompt": [{"type": "text", "text": "fix the bug"}]}))
    assert resp["result"]["stopReason"] == "end_turn"
    upd = [m for m in sent if m.get("method") == "session/update"]
    assert upd and upd[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert "did: fix the bug" in upd[0]["params"]["update"]["content"]["text"]
    assert upd[0]["params"]["update"]["content"]["type"] == "text"


def test_session_prompt_unknown_session_is_an_error():
    resp = acp.AcpAgent().handle(_req(3, "session/prompt", {"sessionId": "nope", "prompt": []}))
    assert "error" in resp and resp["error"]["code"] == -32602


def test_unknown_method_errors_but_notification_is_silent():
    a = acp.AcpAgent()
    assert a.handle(_req(4, "bogus/method"))["error"]["code"] == -32601      # request → error
    # a notification (no id) never gets a response, even cancel for an unknown session
    assert a.handle({"jsonrpc": "2.0", "method": "session/cancel",
                     "params": {"sessionId": "x"}}) is None


def test_serve_loop_over_stdio_roundtrips():
    instream = io.StringIO(
        json.dumps(_req(1, "initialize", {"protocolVersion": 1})) + "\n"
        + json.dumps(_req(2, "session/new", {"cwd": "/r"})) + "\n")
    out = io.StringIO()
    acp.serve(acp.AcpAgent(), instream=instream, outstream=out)
    lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    assert lines[0]["id"] == 1 and "result" in lines[0]
    assert lines[1]["id"] == 2 and lines[1]["result"]["sessionId"]


# ── capability honesty ──────────────────────────────────────────────────────────

def test_initialize_advertises_embedded_context():
    # We route embedded-context blocks into the prompt text, so advertise it true.
    r = acp.AcpAgent().handle(_req(1, "initialize", {"protocolVersion": 1}))["result"]
    caps = r["agentCapabilities"]["promptCapabilities"]
    assert caps["embeddedContext"] is True
    assert caps["image"] is False and caps["audio"] is False  # we can't process these


# ── richer prompt parsing (text + resource_link + embedded resource) ────────────

def test_prompt_text_includes_resource_links_and_embedded_resources():
    blocks = [
        {"type": "text", "text": "review this:"},
        {"type": "resource_link", "uri": "file:///repo/src/a.py", "name": "a.py"},
        {"type": "resource", "resource": {"uri": "file:///repo/b.md", "text": "B CONTENTS"}},
        {"type": "image", "data": "..."},  # unprocessable → dropped, must not crash
    ]
    out = acp.prompt_text(blocks)
    assert "review this:" in out
    assert "a.py" in out               # link surfaced (name or uri)
    assert "B CONTENTS" in out         # embedded resource text inlined


def test_prompt_text_text_only_unchanged():
    assert acp.prompt_text([{"type": "text", "text": "hi"}]) == "hi"


# ── per-session emit wiring ─────────────────────────────────────────────────────

def test_session_prompt_wires_a_session_scoped_emit():
    sent = []
    captured = {}

    def run_turn(text, sess):
        captured["emit"] = sess.get("_emit")
        return {"text": "", "stop_reason": "end_turn"}

    a = acp.AcpAgent(run_turn=run_turn, send=sent.append)
    sid = a.handle(_req(1, "session/new", {"cwd": "/r"}))["result"]["sessionId"]
    a.handle(_req(2, "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "go"}]}))
    emit = captured["emit"]
    assert callable(emit)
    # the emit sends a properly-shaped session/update for THIS session
    emit({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hello"}})
    upd = [m for m in sent if m.get("method") == "session/update"]
    assert upd[-1]["params"]["sessionId"] == sid
    assert upd[-1]["params"]["update"]["content"]["text"] == "hello"


# ── tool-call update builders ───────────────────────────────────────────────────

def test_tool_kind_maps_korgex_tools_to_acp_kinds():
    assert acp.tool_kind("Read") == "read"
    assert acp.tool_kind("Edit") == "edit" and acp.tool_kind("Write") == "edit"
    assert acp.tool_kind("Bash") == "execute"
    assert acp.tool_kind("Grep") == "search" and acp.tool_kind("Glob") == "search"
    assert acp.tool_kind("WebFetch") == "fetch" and acp.tool_kind("WebSearch") == "fetch"
    assert acp.tool_kind("delete_file") == "delete"
    assert acp.tool_kind("SomethingElse") == "other"


def test_tool_call_begin_builds_an_in_progress_tool_call():
    upd = acp.tool_call_begin({"id": "tc1", "name": "Read", "args": {"file_path": "src/a.py"}})
    assert upd["sessionUpdate"] == "tool_call"
    assert upd["toolCallId"] == "tc1"
    assert upd["kind"] == "read"
    assert upd["status"] == "in_progress"
    assert "a.py" in upd["title"]      # human-readable label from the args
    assert "content" not in upd        # a read carries no diff


def test_tool_call_diff_for_edit_and_write():
    edit = acp.tool_call_diff({"name": "Edit",
                               "args": {"file_path": "src/a.py", "old_string": "foo", "new_string": "bar"}})
    assert edit == {"type": "diff", "path": "src/a.py", "oldText": "foo", "newText": "bar"}
    write = acp.tool_call_diff({"name": "Write",
                                "args": {"file_path": "src/new.py", "content": "x = 1\n"}})
    assert write == {"type": "diff", "path": "src/new.py", "oldText": "", "newText": "x = 1\n"}


def test_tool_call_diff_none_for_non_edits_or_missing_args():
    assert acp.tool_call_diff({"name": "Read", "args": {"file_path": "a.py"}}) is None
    assert acp.tool_call_diff({"name": "Bash", "args": {"command": "ls"}}) is None
    assert acp.tool_call_diff({"name": "Edit", "args": {"file_path": "a.py"}}) is None  # no old/new
    assert acp.tool_call_diff({"name": "Edit", "args": {}}) is None                     # no path


def test_tool_call_begin_includes_a_diff_for_edits():
    upd = acp.tool_call_begin({"id": "e1", "name": "Edit",
                               "args": {"file_path": "a.py", "old_string": "x", "new_string": "y"}})
    assert upd["kind"] == "edit"
    assert upd["content"] == [{"type": "diff", "path": "a.py", "oldText": "x", "newText": "y"}]


def test_tool_call_end_completed_and_failed():
    ok = acp.tool_call_end({"id": "tc2", "name": "Bash", "args": {"command": "pytest"}},
                           {"output": "all passed"})
    assert ok["sessionUpdate"] == "tool_call_update"
    assert ok["toolCallId"] == "tc2" and ok["status"] == "completed"

    bad = acp.tool_call_end({"id": "tc3", "name": "Edit", "args": {"file_path": "x"}},
                            {"error": "stale_file"})
    assert bad["status"] == "failed"


def test_assistant_text_chunk_shape():
    upd = acp.assistant_text_chunk("thinking out loud")
    assert upd["sessionUpdate"] == "agent_message_chunk"
    assert upd["content"] == {"type": "text", "text": "thinking out loud"}


# ── register_streaming: plugin observers → emitted ACP updates ──────────────────

class _FakePlugins:
    """Mimics PluginRegistry.register/invoke for the streaming wiring test."""
    def __init__(self):
        self._h = {}

    def register(self, hook, fn):
        self._h.setdefault(hook, []).append(fn)

    def invoke(self, hook, *a, **k):
        return [fn(*a, **k) for fn in self._h.get(hook, ())]


def test_register_streaming_translates_hooks_to_updates():
    emitted = []
    plugins = _FakePlugins()
    acp.register_streaming(plugins, emitted.append)

    plugins.invoke("pre_tool", {"id": "t1", "name": "Read", "args": {"file_path": "a.py"}})
    plugins.invoke("post_tool", {"call": {"id": "t1", "name": "Read", "args": {"file_path": "a.py"}},
                                 "result": {"content": "..."}})
    plugins.invoke("on_assistant_text", {"text": "here's the plan"})

    kinds = [u["sessionUpdate"] for u in emitted]
    assert kinds == ["tool_call", "tool_call_update", "agent_message_chunk"]
    assert emitted[0]["toolCallId"] == "t1" and emitted[1]["status"] == "completed"
    assert emitted[2]["content"]["text"] == "here's the plan"


# ── make_live_run_turn: the live bridge (streaming + dedup + error) ─────────────

from src.plugins import PluginRegistry  # noqa: E402


class _StubAgent:
    """Drives the real plugin lifecycle the way KorgexAgent.run_task would."""
    def __init__(self):
        self.plugins = PluginRegistry()
        self.repo_root = None

    def run_task(self, prompt):
        self.plugins.invoke("pre_tool", {"id": "t1", "name": "Read", "args": {"file_path": "a.py"}})
        self.plugins.invoke("post_tool", {"call": {"id": "t1", "name": "Read", "args": {"file_path": "a.py"}},
                                          "result": {"content": "file body"}})
        self.plugins.invoke("on_assistant_text", {"text": "the answer is 42"})
        return {"success": True, "result": "the answer is 42"}


def test_live_run_turn_streams_tool_calls_and_dedups_final_text():
    sent = []
    rt = acp.make_live_run_turn(lambda: _StubAgent())
    a = acp.AcpAgent(run_turn=rt, send=sent.append)
    sid = a.handle(_req(1, "session/new", {"cwd": "/r"}))["result"]["sessionId"]
    resp = a.handle(_req(2, "session/prompt",
                         {"sessionId": sid, "prompt": [{"type": "text", "text": "go"}]}))
    assert resp["result"]["stopReason"] == "end_turn"
    updates = [m["params"]["update"] for m in sent if m.get("method") == "session/update"]
    assert [u["sessionUpdate"] for u in updates] == \
        ["tool_call", "tool_call_update", "agent_message_chunk"]
    # the final answer was streamed exactly ONCE (the protocol layer didn't re-send it)
    answers = [u for u in updates if u["sessionUpdate"] == "agent_message_chunk"
               and u["content"]["text"] == "the answer is 42"]
    assert len(answers) == 1


class _BoomAgent:
    def __init__(self):
        self.plugins = PluginRegistry()
        self.repo_root = None

    def run_task(self, prompt):
        raise RuntimeError("kaboom")


def test_live_run_turn_error_becomes_a_refusal_chunk():
    sent = []
    rt = acp.make_live_run_turn(lambda: _BoomAgent())
    a = acp.AcpAgent(run_turn=rt, send=sent.append)
    sid = a.handle(_req(1, "session/new", {}))["result"]["sessionId"]
    resp = a.handle(_req(2, "session/prompt",
                         {"sessionId": sid, "prompt": [{"type": "text", "text": "x"}]}))
    assert resp["result"]["stopReason"] == "refusal"
    chunks = [m["params"]["update"] for m in sent if m.get("method") == "session/update"]
    assert any("kaboom" in u.get("content", {}).get("text", "") for u in chunks)


# ── slice 2: session/request_permission round-trip ──────────────────────────────

def test_permission_options_offer_allow_allow_always_reject():
    opts = acp.permission_options()
    kinds = {o["kind"] for o in opts}
    assert kinds == {"allow_once", "allow_always", "reject_once"}
    assert all(o.get("optionId") and o.get("name") for o in opts)


def test_permission_params_shape():
    p = acp.permission_params("sid-1", {"toolCallId": "tc", "title": "Edit a.py", "kind": "edit"})
    assert p["sessionId"] == "sid-1"
    assert p["toolCall"]["toolCallId"] == "tc"
    assert isinstance(p["options"], list) and p["options"]


def test_interpret_permission_outcomes():
    sel = lambda oid: {"outcome": {"outcome": "selected", "optionId": oid}}
    assert acp.interpret_permission(sel("allow_once")) == {"allowed": True, "always": False}
    assert acp.interpret_permission(sel("allow_always")) == {"allowed": True, "always": True}
    assert acp.interpret_permission(sel("reject_once")) == {"allowed": False, "always": False}
    # cancelled / missing / malformed all fail safe to denied
    assert acp.interpret_permission({"outcome": {"outcome": "cancelled"}})["allowed"] is False
    assert acp.interpret_permission(None)["allowed"] is False
    assert acp.interpret_permission({})["allowed"] is False


def test_make_confirmer_calls_requester_and_handles_always():
    calls = {"always": 0}
    # requester(tool_call) -> {allowed, always}
    confirm_allow = acp.make_confirmer(lambda tc: {"allowed": True, "always": False})
    assert confirm_allow("src/a.py") is True

    confirm_always = acp.make_confirmer(lambda tc: {"allowed": True, "always": True},
                                        on_always=lambda: calls.__setitem__("always", calls["always"] + 1))
    assert confirm_always("src/b.py") is True
    assert calls["always"] == 1                      # the "don't ask again" hook fired

    confirm_reject = acp.make_confirmer(lambda tc: {"allowed": False, "always": False})
    assert confirm_reject("src/c.py") is False


def test_session_prompt_wires_a_permission_requester_that_calls_the_client():
    asked = []

    def fake_request(method, params):
        asked.append((method, params))
        return {"outcome": {"outcome": "selected", "optionId": "allow_once"}}

    captured = {}

    def run_turn(text, sess):
        captured["dec"] = sess["_request_permission"]({"toolCallId": "x", "title": "Edit y", "kind": "edit"})
        return {"text": "", "stop_reason": "end_turn"}

    a = acp.AcpAgent(run_turn=run_turn, send=lambda m: None, request=fake_request)
    sid = a.handle(_req(1, "session/new", {}))["result"]["sessionId"]
    a.handle(_req(2, "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "go"}]}))
    assert asked and asked[0][0] == "session/request_permission"
    assert asked[0][1]["sessionId"] == sid
    assert captured["dec"] == {"allowed": True, "always": False}


def test_serve_supports_a_blocking_outbound_permission_request():
    # End-to-end over the stdio transport: a turn asks the client for permission;
    # serve() must write the request AND read the response inline, then finish the turn.
    def run_turn(text, session):
        dec = session["_request_permission"]({"toolCallId": "t", "title": "Edit a.py", "kind": "edit"})
        return {"text": f"allowed={dec['allowed']}", "stop_reason": "end_turn"}

    instream = io.StringIO(
        json.dumps(_req(1, "session/new", {"cwd": "/r"})) + "\n"
        + json.dumps(_req(2, "session/prompt",
                          {"sessionId": "korgex-1", "prompt": [{"type": "text", "text": "edit it"}]})) + "\n"
        # the client's permission RESPONSE (id must match the agent's outbound request id)
        + json.dumps({"jsonrpc": "2.0", "id": "korgex-req-1",
                      "result": {"outcome": {"outcome": "selected", "optionId": "allow_once"}}}) + "\n")
    out = io.StringIO()
    acp.serve(acp.AcpAgent(run_turn=run_turn), instream=instream, outstream=out)
    msgs = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    # the agent sent an outbound session/request_permission request...
    reqs = [m for m in msgs if m.get("method") == "session/request_permission"]
    assert reqs and reqs[0]["id"] == "korgex-req-1"
    # ...and the turn saw the granted decision (streamed back as a chunk)
    chunks = [m for m in msgs if m.get("method") == "session/update"]
    assert any("allowed=True" in m["params"]["update"]["content"]["text"] for m in chunks)


class _ConfirmAgent:
    """An agent whose gated edit consults `_edit_confirmer` — the seam the bridge
    routes to session/request_permission."""
    def __init__(self):
        self.plugins = PluginRegistry()
        self.repo_root = None
        self.edit_policy = "ask"
        self._edit_confirmer = None

    def run_task(self, prompt):
        allowed = self._edit_confirmer("src/x.py") if self._edit_confirmer else False
        return {"success": True, "result": f"edit allowed={allowed}"}


def test_live_run_turn_routes_edit_gate_to_client_permission():
    sent = []
    rt = acp.make_live_run_turn(lambda: _ConfirmAgent())
    a = acp.AcpAgent(
        run_turn=rt, send=sent.append,
        request=lambda method, params: {"outcome": {"outcome": "selected", "optionId": "allow_once"}})
    sid = a.handle(_req(1, "session/new", {}))["result"]["sessionId"]
    a.handle(_req(2, "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "edit it"}]}))
    chunks = [m["params"]["update"] for m in sent if m.get("method") == "session/update"]
    assert any("edit allowed=True" in u.get("content", {}).get("text", "") for u in chunks)


def test_live_run_turn_denied_edit_is_not_allowed():
    sent = []
    rt = acp.make_live_run_turn(lambda: _ConfirmAgent())
    a = acp.AcpAgent(
        run_turn=rt, send=sent.append,
        request=lambda method, params: {"outcome": {"outcome": "cancelled"}})
    sid = a.handle(_req(1, "session/new", {}))["result"]["sessionId"]
    a.handle(_req(2, "session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "edit it"}]}))
    chunks = [m["params"]["update"] for m in sent if m.get("method") == "session/update"]
    assert any("edit allowed=False" in u.get("content", {}).get("text", "") for u in chunks)
