"""Auditable network capture — the engine behind `korgex` run-under-capture.

Pins the load-bearing pieces: the CA signs per-host leaf certs; the CONNECT path
really terminates TLS with a leaf the client trusts (the interception bit); a plaintext run
is captured end-to-end through a real subprocess; secret header values + secret-shaped
bodies are redacted before any record leaves the module. The full HTTPS-to-a-real-
endpoint round-trip is dogfooded on the wire (needs network/a valid upstream cert).
"""
from __future__ import annotations

import os
import socket
import socketserver
import ssl
import sys
import threading

from src import netcapture as NC


def test_ensure_ca_and_leaf_signed(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_NETCAPTURE_DIR", str(tmp_path))
    ca = NC.ensure_ca()
    assert b"BEGIN CERTIFICATE" in ca["cert_pem"]
    leaf_pem, key_pem = NC.leaf_cert("example.com", ca)
    from cryptography import x509
    leaf = x509.load_pem_x509_certificate(leaf_pem)
    assert leaf.issuer == ca["cert"].subject                      # signed by the CA
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "example.com" in [n.value for n in san]                # leaf is FOR the host
    # CA persists (cached, not regenerated)
    assert NC.ensure_ca()["cert_pem"] == ca["cert_pem"]


def test_capture_record_redacts_secrets():
    body_secret = "sk-ant-" + "A" * 40          # a real provider-key SHAPE
    rec = NC._capture_record(
        "POST", "https://api.x/login",
        {"Authorization": "Bearer xyz", "Cookie": "session=abc", "Content-Type": "application/json"},
        ('{"token": "%s"}' % body_secret).encode(), 401,
        {"Set-Cookie": "s=1"}, b'{"error":"no auth"}', 7)
    blob = __import__("json").dumps(rec)
    # secret header VALUES masked by name; a real-shape secret scrubbed from the body
    assert rec["req_headers"]["Authorization"] == "[REDACTED]"
    assert rec["req_headers"]["Cookie"] == "[REDACTED]"
    assert rec["resp_headers"]["Set-Cookie"] == "[REDACTED]"
    assert body_secret not in blob
    # non-secret data is preserved so the agent can still debug
    assert rec["req_headers"]["Content-Type"] == "application/json"
    assert rec["status"] == 401 and rec["method"] == "POST"
    assert "no auth" in rec["resp_body"]


def test_connect_establishes_tls_tunnel_with_trusted_leaf(tmp_path, monkeypatch):
    # The risky path: CONNECT host:443 → the proxy must terminate TLS presenting a leaf
    # the client trusts (signed by the korgex CA) FOR that host. No upstream needed.
    monkeypatch.setenv("KORGEX_NETCAPTURE_DIR", str(tmp_path))
    proxy = NC.CaptureProxy()
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        s = socket.create_connection(("127.0.0.1", proxy.port), timeout=10)
        s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        assert b"200" in s.recv(4096).split(b"\r\n", 1)[0]
        ctx = ssl.create_default_context(cafile=os.path.join(str(tmp_path), "ca.pem"))
        tls = ctx.wrap_socket(s, server_hostname="example.com")   # verifies against korgex CA
        names = [v for typ, v in tls.getpeercert().get("subjectAltName", ()) if typ == "DNS"]
        assert "example.com" in names
        tls.close()
    finally:
        proxy.shutdown()
        proxy.server_close()


def _local_http_server():
    class H(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("X-Test", "yes")
            self.end_headers()
            self.wfile.write(b"HELLO-FROM-APP")

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_run_with_capture_records_http_exchange_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_NETCAPTURE_DIR", str(tmp_path))
    srv, port = _local_http_server()
    try:
        client = (
            "import urllib.request,os,sys;"
            "op=urllib.request.build_opener("
            "urllib.request.ProxyHandler({'http': os.environ['HTTP_PROXY']}));"
            f"r=op.open('http://127.0.0.1:{port}/hello');"
            "sys.stdout.write(r.read().decode())"
        )
        res = NC.run_with_capture([sys.executable, "-c", client], timeout=40)
        assert res["exit_code"] == 0, res
        assert "HELLO-FROM-APP" in res["stdout"]
        assert res["count"] >= 1
        got = [c for c in res["captures"] if c["url"].endswith("/hello")]
        assert got and got[0]["method"] == "GET" and got[0]["status"] == 200
        assert "HELLO-FROM-APP" in got[0]["resp_body"]
    finally:
        srv.shutdown()


def test_run_with_capture_fails_soft_on_bad_command(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_NETCAPTURE_DIR", str(tmp_path))
    res = NC.run_with_capture(["this-binary-does-not-exist-korgex"], timeout=10)
    assert "error" in res and res["captures"] == []   # never raises into the loop


# ── the agent tool (handler + opt-in registration) ───────────────────────────

def test_tool_net_capture_refuses_destructive_command():
    from src import tools_impl
    out = tools_impl.tool_net_capture("rm -rf /")
    assert out.get("verdict") == "DESTRUCTIVE_BLOCKED"   # same floor as Bash


def test_tool_net_capture_runs_command(tmp_path, monkeypatch):
    monkeypatch.setenv("KORGEX_NETCAPTURE_DIR", str(tmp_path))
    from src import tools_impl
    out = tools_impl.tool_net_capture("echo hello-netcap")
    assert out.get("exit_code") == 0
    assert "hello-netcap" in out.get("stdout", "")
    assert out.get("count") == 0                          # no HTTP traffic


def test_netcapture_tool_registers_on_demand():
    # Opt-in (default off), so register on demand for this assertion + clean up.
    from src.tool_abstraction import register_netcapture_tool, USER_TOOLS
    register_netcapture_tool()
    try:
        assert "NetCapture" in USER_TOOLS
        t = USER_TOOLS["NetCapture"]
        assert t["exposure"] == "direct"
        assert t["input_schema"]["required"] == ["command"]
    finally:
        USER_TOOLS.pop("NetCapture", None)
