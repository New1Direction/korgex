"""TOOL BRIDGE (kernel side) — thin RPC stubs injected into the kernel namespace.

The kernel NEVER imports korgex tools (no ungoverned copies). Instead each stub
emits a ``tool_call`` on the wire and BLOCKS reading its stdin for the matching
``tool_result`` — exactly like a normal function call/return. The parent runs the
SAME governed gate stack + ledger the serial loop runs, so a code-driven Write to
``.git`` is blocked identically to a direct Write.

``make_stubs(send_fn, recv_fn, exec_id_getter)`` returns the dict of bridge
functions. ``send_fn(obj)`` writes a framed JSON line to stdout and flushes;
``recv_fn(call_id)`` blocks reading stdin for the ``tool_result`` whose
``call_id`` matches, returning ``(ok, payload)``. ``exec_id_getter()`` returns the
current exec request's id so each ``tool_call`` is stamped for parent-side
correlation (a stray call from a dead exec can be rejected).
"""

from __future__ import annotations

import threading
import uuid
from typing import Callable

from . import protocol as P

# stub function name -> USER-FACING tool name routed by the parent's
# route_tool_call / _TOOL_ROUTING. Kept here so the name-map lives in ONE place.
NAME_MAP = {
    "read_file": "Read",
    "write_file": "Write",
    "edit": "Edit",
    "bash": "Bash",
    "glob": "Glob",
    "grep": "Grep",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "Retrieve": "Retrieve",
}


def make_stubs(send_fn: Callable[[dict], None],
               recv_fn: Callable[[str], tuple],
               exec_id_getter: Callable[[], str]) -> dict:
    """Build the bridge-function namespace injected into the kernel GLOBALS.

    Every stub is a thin RPC: build a fresh ``call_id``, send a ``tool_call``,
    block on ``recv_fn`` for the matching ``tool_result``, then return the
    plain-dict result on ``ok`` or raise ``RuntimeError(error)`` on failure so
    user code sees a normal exception.
    """

    # SINGLE-FLIGHT: the protocol allows at most ONE outstanding tool_call at a
    # time (the parent services them serially off one pipe). If user code calls
    # tools from multiple threads, unsynchronized send+recv would interleave on the
    # wire and wedge the kernel. This lock makes each round-trip atomic, so
    # concurrent stub calls serialize instead of corrupting the wire.
    _wire_lock = threading.Lock()

    def _rpc(user_facing_name: str, args: dict):
        call_id = uuid.uuid4().hex
        with _wire_lock:
            send_fn(P.tool_call(call_id, exec_id_getter(), user_facing_name, args))
            ok, payload = recv_fn(call_id)
        if ok:
            return payload
        raise RuntimeError(str(payload))

    # Named stubs use the SAME param names the model-facing schema uses, so code
    # reads naturally (read_file(file_path=...), edit(file_path=, old_string=, ...)).
    def read_file(file_path):
        return _rpc("Read", {"file_path": file_path})

    def write_file(file_path, content):
        return _rpc("Write", {"file_path": file_path, "content": content})

    def edit(file_path, old_string, new_string):
        return _rpc("Edit", {"file_path": file_path,
                             "old_string": old_string, "new_string": new_string})

    def bash(command):
        return _rpc("Bash", {"command": command})

    def glob(path):
        return _rpc("Glob", {"path": path})

    def grep(pattern, path=None):
        args = {"pattern": pattern}
        if path is not None:
            args["path"] = path
        return _rpc("Grep", args)

    def web_search(query):
        return _rpc("WebSearch", {"query": query})

    def web_fetch(url):
        return _rpc("WebFetch", {"url": url})

    def Retrieve(ref):  # noqa: N802 — user-facing name matches the tool
        return _rpc("Retrieve", {"ref": ref})

    def call_tool(name, **kwargs):
        """Escape hatch — reach ANY routed tool (browser_*, MCP, …) by exact name."""
        return _rpc(name, dict(kwargs))

    return {
        "read_file": read_file,
        "write_file": write_file,
        "edit": edit,
        "bash": bash,
        "glob": glob,
        "grep": grep,
        "web_search": web_search,
        "web_fetch": web_fetch,
        "Retrieve": Retrieve,
        "call_tool": call_tool,
    }
