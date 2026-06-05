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
loop runs, not one blob after it.

Permission: when korgex can't auto-allow an edit (a sensitive path, or `KORGEX_EDIT_POLICY=ask`),
its edit gate calls back to the client with `session/request_permission` (a BLOCKING agent→client
request the stdio transport services inline) and honors the choice — "allow once", "allow (don't
ask again)" relaxes the policy for the session, or "reject".

Cancel: `serve()` reads stdin on a background thread, so a `session/cancel` arriving mid-turn
flips the session's cancel flag immediately; the agent loop checks it between rounds and stops
cleanly (an editor's "stop" actually interrupts).

Resume + MCP: `session/load` re-attaches to a prior session by id and the bridge seeds the
first turn with the transcript rebuilt from the repo's ledger (continuity across restarts).
An editor's configured `mcpServers` (forwarded on session/new or session/load) are translated
and connected into the agent's tool surface.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import sys
import threading

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

    def __init__(self, *, run_turn=None, send=None, request=None):
        self.run_turn = run_turn
        self.send = send
        # request(method, params) -> result: a BLOCKING agent→client call (e.g.
        # session/request_permission). serve() supplies one that writes the request
        # and reads its response inline; None means no client to ask (deny-safe).
        self.request = request
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
            elif method == "session/load":
                result = self._session_load(params)
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
                "loadSession": True,
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

    def _session_load(self, params: dict) -> dict:
        """Re-attach to a prior session by id. We register it under the client's id and
        mark it `resumed` — the bridge then seeds the first turn with the prior
        transcript reconstructed from the repo's ledger (continuity across restarts)."""
        sid = params.get("sessionId")
        if not sid:
            raise AcpError(-32602, "session/load requires a sessionId")
        self.sessions[sid] = {"cwd": params.get("cwd"),
                              "mcpServers": params.get("mcpServers") or [],
                              "cancelled": False, "resumed": True}
        return {}

    def _session_prompt(self, params: dict) -> dict:
        sid = params.get("sessionId")
        session = self.sessions.get(sid)
        if session is None:
            raise AcpError(-32602, f"unknown session: {sid}")
        session["cancelled"] = False
        # Session-scoped emit: the turn (and any plugins it registers) streams
        # agent_message_chunk / tool_call updates back to THIS session as it works.
        session["_emit"] = self._emitter(sid)
        # Session-scoped permission requester: the turn asks the client to approve a
        # gated action (an edit it can't auto-allow) and gets back {allowed, always}.
        session["_request_permission"] = lambda tc: self._request_permission(sid, tc)
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

    def _request_permission(self, sid: str, tool_call: dict) -> dict:
        """Ask the client to approve `tool_call` for this session; return the decision
        ``{allowed, always}``. With no client request channel, fail safe to denied."""
        if not self.request:
            return {"allowed": False, "always": False}
        res = self.request("session/request_permission",
                           permission_params(sid, tool_call))
        return interpret_permission(res)


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


def tool_call_diff(call: dict):
    """An ACP `diff` content block previewing an edit, or None for non-edit tools.
    Built from the call args alone (no filesystem read): an `Edit` shows its
    old_string→new_string fragments; a `Write` shows the new file content (old side
    empty). The editor renders this as an inline diff on the tool-call card."""
    name = call.get("name")
    args = call.get("args") or {}
    path = args.get("file_path")
    if not path:
        return None
    if name == "Edit":
        old, new = args.get("old_string"), args.get("new_string")
        if old is None or new is None:
            return None
        return {"type": "diff", "path": path, "oldText": old, "newText": new}
    if name == "Write":
        content = args.get("content")
        if content is None:
            return None
        return {"type": "diff", "path": path, "oldText": "", "newText": content}
    return None


def tool_call_begin(call: dict) -> dict:
    """ACP `tool_call` update (status in_progress) — the editor shows an activity card,
    with an inline diff preview for edits."""
    upd = {
        "sessionUpdate": "tool_call",
        "toolCallId": str(call.get("id") or ""),
        "title": _tool_title(call),
        "kind": tool_kind(call.get("name")),
        "status": "in_progress",
    }
    diff = tool_call_diff(call)
    if diff is not None:
        upd["content"] = [diff]
    return upd


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


# ── permission round-trip (agent→client session/request_permission) ─────────────

_ALLOW_OPTION_IDS = ("allow_once", "allow_always")


def permission_options() -> list:
    """The approval choices offered to the client: allow once, allow for the rest of
    the session ("don't ask again"), or reject. `kind` uses ACP's snake_case enum."""
    return [
        {"optionId": "allow_once", "name": "Allow", "kind": "allow_once"},
        {"optionId": "allow_always", "name": "Allow (don't ask again)", "kind": "allow_always"},
        {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
    ]


def permission_params(session_id: str, tool_call: dict, options=None) -> dict:
    """Params for a `session/request_permission` request: the session, the tool call
    awaiting approval, and the options the client may pick from."""
    return {
        "sessionId": session_id,
        "toolCall": tool_call,
        "options": options if options is not None else permission_options(),
    }


def interpret_permission(response) -> dict:
    """Map a `session/request_permission` response to ``{allowed, always}``. Only an
    explicit selection of an allow option proceeds; cancelled / missing / malformed
    all fail safe to denied."""
    outcome = (response or {}).get("outcome") if isinstance(response, dict) else None
    if isinstance(outcome, dict) and outcome.get("outcome") == "selected":
        oid = outcome.get("optionId")
        return {"allowed": oid in _ALLOW_OPTION_IDS, "always": oid == "allow_always"}
    return {"allowed": False, "always": False}


def make_confirmer(requester, *, on_always=None):
    """Build a ``confirm(path) -> bool`` for korgex's edit gate from a session-bound
    ``requester(tool_call) -> {allowed, always}``. On an "allow always" decision it
    fires ``on_always`` (so the bridge can stop re-asking this session) and allows."""
    def confirm(path: str) -> bool:
        tool_call = {"toolCallId": f"edit:{path}", "title": f"Edit {path}", "kind": "edit"}
        decision = requester(tool_call) or {}
        if decision.get("always") and on_always:
            on_always()
        return bool(decision.get("allowed"))
    return confirm


def _noop_turn(text: str, session: dict) -> dict:
    """Default runner when none is injected — echoes, so a bare server is still well-formed."""
    return {"text": "", "stop_reason": "end_turn"}


def mcp_servers_to_config(acp_servers) -> dict:
    """Translate ACP `session/new`/`session/load` ``mcpServers`` into korgex's config
    shape ``{name: {command,args,env | url,headers}}`` — so an editor's configured MCP
    servers become part of the agent's tool surface. Entries without a name are dropped."""
    out: dict = {}
    for s in acp_servers or []:
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if not name:
            continue
        if s.get("url"):
            cfg = {"url": s["url"]}
            if s.get("headers"):
                cfg["headers"] = s["headers"]
        else:
            cfg = {"command": s.get("command")}
            if s.get("args"):
                cfg["args"] = s["args"]
            if s.get("env"):
                cfg["env"] = s["env"]
        out[name] = cfg
    return out


def _resume_context_for(cwd):
    """Build a resume preamble from a repo's ledger journal, or None. Used for
    session/load so a reloaded session continues with its prior transcript."""
    if not cwd:
        return None
    journal = os.environ.get("KORG_JOURNAL_PATH") or os.path.join(cwd, ".korg", "journal.jsonl")
    if not os.path.isfile(journal):
        return None
    try:
        from src import resume as _R
        ctx = _R.build_resume_context(journal)
        return _R.resume_preamble(ctx) if ctx else None
    except Exception:
        return None


def make_live_run_turn(agent_factory, *, resume_builder=None):
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
                # Route the agent's edit-approval gate to the editor: when korgex
                # can't auto-allow an edit (sensitive path, or KORGEX_EDIT_POLICY=ask),
                # it asks the client via session/request_permission instead of its own
                # prompt. "Allow (don't ask again)" relaxes the policy for the session.
                requester = session.get("_request_permission")
                if requester is not None and hasattr(agent, "_edit_confirmer"):
                    agent._edit_confirmer = make_confirmer(
                        requester,
                        on_always=lambda: setattr(agent, "edit_policy", "free"),
                    )
                # Cooperative cancel: the agent loop checks this between rounds, so a
                # session/cancel (flipped by the transport's reader thread) stops the turn.
                if hasattr(agent, "_should_cancel"):
                    agent._should_cancel = lambda: bool(session.get("cancelled"))
                # Forward the editor's configured MCP servers into the agent's tool
                # surface (the agent doesn't auto-load mcp.json in ACP mode).
                servers = session.get("mcpServers")
                if servers and hasattr(agent, "connect_mcp_configs"):
                    try:
                        agent.connect_mcp_configs(mcp_servers_to_config(servers))
                    except Exception:
                        pass
                # Resume a loaded session: seed the FIRST turn with the prior transcript
                # rebuilt from the repo's ledger. Built once per session.
                resume_ctx = None
                if session.get("resumed") and not session.get("_resume_built"):
                    session["_resume_built"] = True
                    try:
                        resume_ctx = (resume_builder or _resume_context_for)(session.get("cwd"))
                    except Exception:
                        resume_ctx = None
                if resume_ctx:
                    result = agent.run_task(prompt, resume_context=resume_ctx) or {}
                else:
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
    """Run the ACP agent loop over stdio until EOF.

    A **background reader thread** owns stdin: a `session/cancel` notification is handled
    the moment it arrives (it just flips the session's cancel flag, which a running turn
    checks between rounds — so an editor's "stop" actually interrupts mid-turn); every
    other message is put on a queue the main thread dispatches serially. The agent can
    also make a BLOCKING outbound request (`agent.request`, e.g. session/request_permission)
    — it consumes from the same queue, so it sees the client's response even though the
    reader thread is the only one touching stdin."""
    instream = instream if instream is not None else sys.stdin
    outstream = outstream if outstream is not None else sys.stdout
    agent.send = lambda m: write_message(outstream, m)
    inbox: queue.Queue = queue.Queue()

    def reader() -> None:
        while True:
            try:
                msg = read_message(instream)
            except (ValueError, json.JSONDecodeError):
                continue  # skip a malformed line rather than kill the reader
            if msg is None:
                inbox.put(None)  # EOF sentinel
                return
            # session/cancel is handled INLINE here (not queued) so an in-flight turn
            # on the main thread sees the flag immediately. It's a no-response
            # notification, so dispatching it off-thread only flips a bool (GIL-safe).
            if isinstance(msg, dict) and msg.get("method") == "session/cancel" and "id" not in msg:
                agent.handle(msg)
                continue
            inbox.put(msg)

    threading.Thread(target=reader, daemon=True).start()
    _req_id = {"n": 0}

    def request_client(method: str, params: dict):
        """Send an agent→client request and block for its response, servicing any
        intervening (non-cancel) messages from the queue. None on EOF — deny-safe."""
        _req_id["n"] += 1
        rid = f"korgex-req-{_req_id['n']}"  # string id won't collide with client ints
        write_message(outstream, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            reply = inbox.get()
            if reply is None:
                inbox.put(None)  # re-post EOF so the main loop also unblocks + exits
                return None
            if isinstance(reply, dict) and reply.get("id") == rid \
                    and ("result" in reply or "error" in reply):
                return reply.get("result")
            other = agent.handle(reply)
            if other is not None:
                write_message(outstream, other)

    agent.request = request_client

    while True:
        msg = inbox.get()
        if msg is None:
            break
        resp = agent.handle(msg)
        if resp is not None:
            write_message(outstream, resp)
