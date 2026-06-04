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
to stdio. The bridge to the live agent loop, tool-call streaming, `session/request_permission`,
and the fs/terminal client methods are deliberate follow-ups; end-to-end validation needs a
real ACP client.
"""
from __future__ import annotations

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
    """Join the text from a session/prompt content-block array (text blocks only)."""
    return "\n".join(b.get("text", "") for b in (blocks or [])
                     if isinstance(b, dict) and b.get("type") == "text").strip()


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
                "promptCapabilities": {"audio": False, "embeddedContext": False, "image": False},
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


def _noop_turn(text: str, session: dict) -> dict:
    """Default runner when none is injected — echoes, so a bare server is still well-formed."""
    return {"text": "", "stop_reason": "end_turn"}


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
