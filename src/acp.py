"""korgex as an Agent Client Protocol (ACP) agent — JSON-RPC 2.0 over stdio.

ACP (agentclientprotocol.com) lets editors/clients (Zed et al.) drive a coding agent over
a process boundary. This makes korgex one of those agents: a client speaks ACP to korgex's
stdin/stdout and korgex runs its loop, streaming results back. It fits the cross-vendor
positioning — one verifiable agent, drivable from any ACP editor.

Implemented clean-room from the open spec (agentclientprotocol.com):
  - JSON-RPC 2.0, newline-delimited messages over stdio.
  - Client→Agent methods: `initialize`, `session/new`, `session/prompt`; `session/cancel`
    is a notification. Agent→Client: `session/update` notifications (discriminator field
    `sessionUpdate`; content blocks keyed by `type`). Stop reasons: end_turn | max_tokens |
    cancelled | refusal. Object keys are camelCase; discriminator string values snake_case.

`AcpAgent` is transport-agnostic and dependency-injected (`run_turn` does the real work,
`send` emits notifications) so the protocol layer is fully unit-testable. `serve()` wires it
to stdio.

Live streaming: during a turn the bridge registers korgex's plugin lifecycle
(`register_streaming`) so each tool fires a `tool_call`/`tool_call_update` and each round's
narration fires an `agent_message_chunk` — the editor shows activity + streamed text as the
loop runs, not one blob after it. `session/request_permission`, real mid-turn `session/cancel`,
`session/load`, and wiring the client's `mcpServers` are deliberate follow-ups.
"""
from __future__ import annotations

import contextlib
import json
import sys

PROTOCOL_VERSION = 1
_STOP_REASONS = ("end_turn", "max_tokens", "cancelled", "refusal")


class AcpError(Exception):
    """A JSON-RPC error to return for a request (code + message)."""

    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message)


def _response(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def notification(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def prompt_text(blocks) -> str:
    """Join a session/prompt content-block array into one text prompt.

    Pulls plain ``text`` blocks, ``resource_link`` blocks (surfaced as an ``@name``
    reference, matching korgex's @-mention convention) and embedded ``resource``
    blocks (their inline text, wrapped in a ``<context>`` marker). Unprocessable
    blocks (image, audio) are skipped. This is what lets an editor hand korgex
    @-file mentions and pasted context, not just a plain string."""
    parts: list = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind == "text":
            parts.append(b.get("text", ""))
        elif kind == "resource_link":
            ref = b.get("name") or b.get("uri") or ""
            if ref:
                parts.append(f"@{ref}")
        elif kind == "resource":
            res = b.get("resource") or {}
            txt = res.get("text")
            if txt:
                uri = res.get("uri") or ""
                head = f"<context {uri}>" if uri else "<context>"
                parts.append(f"{head}\n{txt}\n</context>")
    return "\n".join(p for p in parts if p).strip()


class AcpAgent:
    """Dispatches ACP JSON-RPC messages, bridging `session/prompt` to a coding-agent turn.

    `run_turn(prompt_text, session) -> {"text": str, "stop_reason": str}` does the real work
    (production: a closure on KorgexAgent.run_task). `send(message)` emits an agent→client
    JSON-RPC message (notification). Both injected so this is testable without a client."""

    def __init__(self, *, run_turn=None, send=None):
        self.run_turn = run_turn
        self.send = send
        self.sessions: dict = {}
        self._n = 0

    # ── dispatch ──────────────────────────────────────────────────────────────
    def handle(self, msg: dict):
        """Process one incoming JSON-RPC message. Returns a response dict for a request, or
        None for a notification (no `id`). Errors on a request become a JSON-RPC error."""
        method = msg.get("method")
        params = msg.get("params") or {}
        is_request = "id" in msg
        mid = msg.get("id")
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "session/new":
                result = self._session_new(params)
            elif method == "session/prompt":
                result = self._session_prompt(params)
            elif method == "session/cancel":
                self._session_cancel(params)
                return None  # notification
            else:
                if is_request:
                    return _error(mid, -32601, f"method not found: {method}")
                return None
        except AcpError as e:
            return _error(mid, e.code, str(e)) if is_request else None
        return _response(mid, result) if is_request else None

    # ── handlers ──────────────────────────────────────────────────────────────
    def _initialize(self, params: dict) -> dict:
        # Echo the client's protocol version when sane; advertise what korgex supports.
        return {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "agentCapabilities": {
                "loadSession": False,
                # embeddedContext: we inline resource/resource_link blocks into the
                # prompt (see prompt_text). image/audio: korgex can't process these.
                "promptCapabilities": {"audio": False, "embeddedContext": True, "image": False},
                "mcpCapabilities": {"http": True, "sse": False},
            },
            "authMethods": [],  # korgex uses BYO-key/config, not an ACP auth handshake
        }

    def _session_new(self, params: dict) -> dict:
        self._n += 1
        sid = f"korgex-{self._n}"
        self.sessions[sid] = {"cwd": params.get("cwd"),
                              "mcpServers": params.get("mcpServers") or [],
                              "cancelled": False}
        return {"sessionId": sid}

    def _session_prompt(self, params: dict) -> dict:
        sid = params.get("sessionId")
        session = self.sessions.get(sid)
        if session is None:
            raise AcpError(-32602, f"unknown session: {sid}")
        session["cancelled"] = False
        # Session-scoped emit: the turn (and any plugins it registers) streams
        # agent_message_chunk / tool_call updates back to THIS session as it works.
        session["_emit"] = self._emitter(sid)
        text = prompt_text(params.get("prompt"))

        out = (self.run_turn or _noop_turn)(text, session) or {}

        if session.get("cancelled"):
            return {"stopReason": "cancelled"}
        # Stream the agent's reply back as an agent_message_chunk update.
        reply = out.get("text")
        if reply and self.send:
            self.send(notification("session/update", {
                "sessionId": sid,
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": reply}},
            }))
        stop = out.get("stop_reason", "end_turn")
        return {"stopReason": stop if stop in _STOP_REASONS else "end_turn"}

    def _session_cancel(self, params: dict) -> None:
        session = self.sessions.get(params.get("sessionId"))
        if session is not None:
            session["cancelled"] = True

    def _emitter(self, sid: str):
        """A session-scoped `emit(update)` that wraps an update dict in a
        `session/update` notification for THIS session and sends it."""
        def emit(update: dict) -> None:
            if self.send:
                self.send(notification("session/update",
                                       {"sessionId": sid, "update": update}))
        return emit


# ── ACP session/update builders (tool-call lifecycle + text) ────────────────────

_TOOL_KINDS = {
    "Read": "read",
    "Edit": "edit", "Write": "edit",
    "Bash": "execute",
    "Grep": "search", "Glob": "search", "list_files": "search",
    "WebFetch": "fetch", "WebSearch": "fetch",
    "delete_file": "delete",
}


def tool_kind(name) -> str:
    """Map a korgex tool name to an ACP ToolKind (read|edit|execute|search|fetch|
    delete|other) so the editor can pick an icon/affordance for the activity card."""
    return _TOOL_KINDS.get(name or "", "other")


def _tool_title(call: dict) -> str:
    """A short human label for the tool-call card: ``Read: src/a.py`` etc."""
    name = call.get("name") or "tool"
    args = call.get("args") or {}
    detail = (args.get("file_path") or args.get("path") or args.get("command")
              or args.get("pattern") or args.get("query") or args.get("url") or "")
    detail = str(detail)
    if len(detail) > 80:
        detail = detail[:77] + "..."
    return f"{name}: {detail}" if detail else str(name)


def _result_summary(result, limit: int = 2000) -> str:
    """A compact text summary of a tool result for the tool_call_update content."""
    if result is None:
        return ""
    if isinstance(result, str):
        s = result
    elif isinstance(result, dict):
        s = (result.get("error") or result.get("output") or result.get("content")
             or result.get("result") or result.get("text") or "")
        if not isinstance(s, str):
            s = json.dumps(result, default=str)
    else:
        s = str(result)
    return s[:limit]


def tool_call_begin(call: dict) -> dict:
    """ACP `tool_call` update (status in_progress) — the editor shows an activity card."""
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": str(call.get("id") or ""),
        "title": _tool_title(call),
        "kind": tool_kind(call.get("name")),
        "status": "in_progress",
    }


def tool_call_end(call: dict, result) -> dict:
    """ACP `tool_call_update` (completed|failed) with a compact text content summary."""
    failed = isinstance(result, dict) and bool(result.get("error"))
    upd = {
        "sessionUpdate": "tool_call_update",
        "toolCallId": str(call.get("id") or ""),
        "status": "failed" if failed else "completed",
    }
    summary = _result_summary(result)
    if summary:
        upd["content"] = [{"type": "content",
                           "content": {"type": "text", "text": summary}}]
    return upd


def assistant_text_chunk(text) -> dict:
    """ACP `agent_message_chunk` update carrying a piece of the model's reply text."""
    return {"sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text}}


def register_streaming(plugins, emit) -> None:
    """Wire korgex's plugin lifecycle to ACP `session/update` emits: each tool fires
    a `tool_call` (begin) then `tool_call_update` (end), and each round's narration
    fires an `agent_message_chunk`. `emit(update)` sends one update for the session.
    Used by the live `korgex acp` bridge so an editor sees tool activity + streamed
    text instead of one opaque blob after the turn."""
    plugins.register("pre_tool", lambda call: emit(tool_call_begin(call or {})))
    plugins.register(
        "post_tool",
        lambda p: emit(tool_call_end((p or {}).get("call") or {}, (p or {}).get("result"))),
    )

    def _on_text(p) -> None:
        text = (p or {}).get("text")
        if text:
            emit(assistant_text_chunk(text))

    plugins.register("on_assistant_text", _on_text)


def _noop_turn(text: str, session: dict) -> dict:
    """Default runner when none is injected — echoes, so a bare server is still well-formed."""
    return {"text": "", "stop_reason": "end_turn"}


def make_live_run_turn(agent_factory):
    """Build the live `run_turn` for `korgex acp`.

    Per turn: construct an agent via ``agent_factory()``, point it at the session's
    cwd, register ACP streaming on its plugin lifecycle, run the task (its stdout
    redirected to stderr so it can't corrupt the JSON-RPC channel), and return
    ``{text, stop_reason}``. Returns ``text=""`` when the reply was already streamed
    as ``agent_message_chunk``s, so the protocol layer doesn't duplicate it. Any
    exception becomes a `refusal` with the error text (never crashes the loop)."""
    def run_turn(prompt: str, session: dict) -> dict:
        try:
            emit = session.get("_emit")
            streamed: list = []
            with contextlib.redirect_stdout(sys.stderr):
                agent = agent_factory()
                if session.get("cwd"):
                    agent.repo_root = session["cwd"]
                if emit:
                    def _emit(update: dict) -> None:
                        if update.get("sessionUpdate") == "agent_message_chunk":
                            streamed.append(update)
                        emit(update)
                    register_streaming(agent.plugins, _emit)
                result = agent.run_task(prompt) or {}
            ok = result.get("success", True)
            text = "" if (streamed and ok) else result.get("result", "")
            return {"text": text, "stop_reason": "end_turn" if ok else "refusal"}
        except Exception as e:  # noqa: BLE001 — never let a turn crash the ACP loop
            return {"text": f"korgex error: {e}", "stop_reason": "refusal"}
    return run_turn


# ── stdio transport (newline-delimited JSON-RPC) ────────────────────────────────
def read_message(stream):
    """Read one newline-delimited JSON-RPC message; None at EOF (or a blank line skipped)."""
    line = stream.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return read_message(stream)
    return json.loads(line)


def write_message(stream, obj: dict) -> None:
    stream.write(json.dumps(obj) + "\n")
    stream.flush()


def serve(agent: AcpAgent, instream=None, outstream=None) -> None:
    """Run the ACP agent loop over stdio until EOF, dispatching each message and writing any
    response. Notifications (e.g. session/update) are emitted via the agent's `send`."""
    instream = instream if instream is not None else sys.stdin
    outstream = outstream if outstream is not None else sys.stdout
    agent.send = lambda m: write_message(outstream, m)
    while True:
        try:
            msg = read_message(instream)
        except (ValueError, json.JSONDecodeError):
            continue  # skip a malformed line rather than crash the loop
        if msg is None:
            break
        resp = agent.handle(msg)
        if resp is not None:
            write_message(outstream, resp)
