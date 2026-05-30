"""A minimal Language Server Protocol client — code intelligence for the agent.

Lets korgex ask a language server for **diagnostics** (errors/types) on a file
instead of editing blind. The protocol + session are deterministic and unit-tested
over byte streams; the real-server path (`diagnostics()`) is best-effort and
time-guarded — it returns ``[]`` and never raises if no server is installed.

    diags = diagnostics("src/app.py")   # → [{"message": "...", "severity": 1, ...}, ...] or []
"""
from __future__ import annotations

import json
import os

# file extension → (language-server command, LSP languageId)
_SERVERS = {
    ".py": (["pyright-langserver", "--stdio"], "python"),
    ".ts": (["typescript-language-server", "--stdio"], "typescript"),
    ".tsx": (["typescript-language-server", "--stdio"], "typescriptreact"),
    ".js": (["typescript-language-server", "--stdio"], "javascript"),
    ".jsx": (["typescript-language-server", "--stdio"], "javascriptreact"),
    ".rs": (["rust-analyzer"], "rust"),
    ".go": (["gopls"], "go"),
}


def encode_message(obj) -> bytes:
    """Frame a JSON-RPC object with the LSP ``Content-Length`` header."""
    body = json.dumps(obj).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


class MessageReader:
    """Reads ``Content-Length``-framed JSON-RPC messages from a binary stream."""

    def __init__(self, stream) -> None:
        self._s = stream

    def read(self):
        header = b""
        while b"\r\n\r\n" not in header:
            ch = self._s.read(1)
            if not ch:
                return None  # EOF
            header += ch
        length = 0
        for line in header.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        body = self._s.read(length)
        if not body:
            return None
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return None


class LspClient:
    """Synchronous JSON-RPC client over a (reader, writer) pair of byte streams."""

    def __init__(self, reader, writer) -> None:
        self._reader = MessageReader(reader)
        self._writer = writer
        self._id = 0
        self._inbox: list[dict] = []  # stashed notifications / server-initiated requests

    def _send(self, obj) -> None:
        self._writer.write(encode_message(obj))
        if hasattr(self._writer, "flush"):
            self._writer.flush()

    def notify(self, method: str, params=None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params=None, max_reads: int = 200):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        for _ in range(max_reads):
            msg = self._reader.read()
            if msg is None:
                return None
            if msg.get("id") == rid:
                return msg.get("result")
            if "method" in msg:
                self._inbox.append(msg)  # a notification arrived before our response
        return None

    def initialize(self, root_uri: str | None = None):
        res = self.request("initialize",
                           {"processId": None, "rootUri": root_uri, "capabilities": {}})
        self.notify("initialized", {})
        return res

    def did_open(self, uri: str, language_id: str, text: str) -> None:
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": language_id, "version": 1, "text": text}})

    def poll_diagnostics(self, uri: str, max_reads: int = 50) -> list:
        def is_diag(m):
            return (m.get("method") == "textDocument/publishDiagnostics"
                    and (m.get("params") or {}).get("uri") == uri)

        for m in list(self._inbox):
            if is_diag(m):
                return m["params"].get("diagnostics", [])
        for _ in range(max_reads):
            msg = self._reader.read()
            if msg is None:
                break
            if is_diag(msg):
                return msg["params"].get("diagnostics", [])
            if "method" in msg:
                self._inbox.append(msg)
        return []

    def shutdown(self) -> None:
        try:
            self.request("shutdown")
            self.notify("exit")
        except Exception:
            pass


def server_for(file_path: str):
    """The language-server command for `file_path`'s extension, or None."""
    entry = _SERVERS.get(os.path.splitext(file_path)[1].lower())
    return entry[0] if entry else None


def _uri(path: str) -> str:
    return "file://" + os.path.abspath(path)


def _drive_server(cmd: list, file_path: str, language_id: str) -> list:
    import subprocess
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    try:
        client = LspClient(proc.stdout, proc.stdin)
        client.initialize(_uri(os.path.dirname(os.path.abspath(file_path))))
        uri = _uri(file_path)
        with open(file_path) as f:
            client.did_open(uri, language_id, f.read())
        diags = client.poll_diagnostics(uri)
        client.shutdown()
        return diags
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


def diagnostics(file_path: str, server_cmd: list | None = None, timeout: float = 10.0) -> list:
    """Open `file_path` in a language server and return its diagnostics — best-effort.

    Returns ``[]`` (never raises) when no server is installed, the extension is
    unknown, the file is missing, or the server hangs past `timeout`.
    """
    import shutil
    import threading

    ext = os.path.splitext(file_path)[1].lower()
    entry = _SERVERS.get(ext)
    cmd = server_cmd or (entry[0] if entry else None)
    language_id = entry[1] if entry else "plaintext"
    if not cmd or not shutil.which(cmd[0]) or not os.path.exists(file_path):
        return []

    result: list = []

    def _run():
        nonlocal result
        try:
            result = _drive_server(cmd, file_path, language_id)
        except Exception:
            result = []

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    return result if not t.is_alive() else []
