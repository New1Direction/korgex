"""MCP reverse-requests — let a server call back to the client.

Most MCP clients are one-way (client → server tool calls). The protocol also
allows the SERVER to make requests of the CLIENT; korgex supports the two
high-value ones:

  - ``elicitation/create``    — the server asks the *user* a question mid-call
                                (e.g. "which environment?") and gets the answer.
  - ``sampling/createMessage`` — the server borrows the *client's LLM* for a
                                completion (so a server can reason without its
                                own model key).

korgex advertises both in its initialize handshake and answers them. The handlers
are pure: the user-asker and the LLM-sampler are injected, so this is testable
with no stdin/network, and both FAIL SAFE — a broken asker declines, a broken
sampler returns a clean JSON-RPC error, never crashing the server's call.
"""
from __future__ import annotations

_REVERSE_METHODS = ("elicitation/create", "sampling/createMessage")
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


def client_capabilities() -> dict:
    """The capabilities korgex advertises so servers know it can be called back."""
    return {
        "tools": {"listChanged": True},
        "elicitation": {},          # we can ask the user on the server's behalf
        "sampling": {},             # we can run the client's LLM on the server's behalf
    }


def is_reverse_request(msg: dict) -> bool:
    """True if `msg` is a server→client REQUEST we should answer (has an id AND a
    known reverse method). A plain response (no method) or a notification (no id)
    is not."""
    return bool(msg.get("method") in _REVERSE_METHODS and msg.get("id") is not None)


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle_elicitation(req_id, params, asker) -> dict:
    """Ask the user the server's question; return accept+content or decline.
    The spec's three actions are accept / decline / cancel; we use accept when the
    user supplies a value, decline otherwise. Never fabricates a value."""
    message = (params or {}).get("message", "")
    schema = (params or {}).get("requestedSchema")
    try:
        answer = asker(message, schema) if asker else ""
    except Exception:
        return _ok(req_id, {"action": "decline"})
    if answer is None or str(answer).strip() == "":
        return _ok(req_id, {"action": "decline"})
    return _ok(req_id, {"action": "accept", "content": answer})


def _handle_sampling(req_id, params, sampler) -> dict:
    """Run the client's LLM for the server. `sampler(messages, system, max_tokens)
    -> str`. Returns a sampling result message; a sampler error is a clean
    JSON-RPC internal error (the server decides how to proceed)."""
    p = params or {}
    messages = p.get("messages", [])
    system = p.get("systemPrompt")
    max_tokens = p.get("maxTokens", 1024)
    try:
        text = sampler(messages, system, max_tokens)
    except Exception as e:
        return _err(req_id, _INTERNAL_ERROR, f"sampling failed: {type(e).__name__}")
    return _ok(req_id, {
        "role": "assistant",
        "content": {"type": "text", "text": text or ""},
        "model": "korgex-client-llm",
        "stopReason": "endTurn",
    })


def handle_reverse(req: dict, *, asker=None, sampler=None) -> dict:
    """Route a server→client request to its handler and build the JSON-RPC
    response. `asker(prompt, schema)->str` and `sampler(messages, system,
    max_tokens)->str` are injected. Unknown methods → method-not-found."""
    req_id = req.get("id")
    method = req.get("method")
    if method == "elicitation/create":
        return _handle_elicitation(req_id, req.get("params"), asker)
    if method == "sampling/createMessage":
        return _handle_sampling(req_id, req.get("params"), sampler)
    return _err(req_id, _METHOD_NOT_FOUND, f"method not found: {method}")
