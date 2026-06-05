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
