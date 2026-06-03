"""Auditable network capture for the dev loop — run an app *under capture*.

korgex agents debug the HTTP(S) of the apps they WRITE without copy-pasting cURL:
``run_with_capture(command)`` launches the command behind a local CA-signing capture
proxy, records every request/response exchange, and returns a structured trace the
agent can reason over ("you POSTed to /login with no auth header → 401").

Boundary + safety (this is a developer DEBUGGER, not an interceptor):
  - **Process-scoped.** The proxy + CA trust are set on the launched command's OWN
    subprocess env (HTTP(S)_PROXY + REQUESTS_CA_BUNDLE/SSL_CERT_FILE/…), never the
    system. Only that app's traffic is seen.
  - **Capture-only.** Requests/responses are observed + forwarded UNMODIFIED — no
    mutation, no replay.
  - **Redacted before it leaves.** Secret header VALUES (Authorization, Cookie, api
    keys) are masked and secret-shaped body content is scrubbed, so the trace — which
    the agent sees AND the ledger records — never carries raw tokens. The agent still
    sees a secret's PRESENCE/ABSENCE (the thing you debug), not its value.
  - **Opt-in.** Surfaced as a tool only when enabled; never bundled into the default.

Known limit (by design): cert-pinned third-party apps won't capture — that's the RE
case korgex doesn't chase. Your own dev apps using normal HTTP clients will.

stdlib + ``cryptography`` only (already a dependency). HTTP/1.1; no HTTP/2 or
websockets (fine for dev debugging).
"""
from __future__ import annotations

import datetime
import gzip
import http.client
import http.server
import os
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
import zlib

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.sanitize import redact as _redact

_SECRET_HEADERS = {
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-api-key", "api-key", "x-auth-token", "x-amz-security-token",
}
_MAX_BODY = 64 * 1024  # cap captured bodies so a big download can't blow up memory


# ── CA + per-host leaf certificates ──────────────────────────────────────────
def _capture_dir() -> str:
    d = os.environ.get("KORGEX_NETCAPTURE_DIR") or os.path.join(
        tempfile.gettempdir(), "korgex-netcapture")
    os.makedirs(d, exist_ok=True)
    return d


def ensure_ca() -> dict:
    """Generate (once, cached in the capture dir) a korgex CA. Returns
    ``{cert_pem, key, cert}`` — the PEM the client must trust + the live objects."""
    d = _capture_dir()
    cert_path, key_path = os.path.join(d, "ca.pem"), os.path.join(d, "ca.key")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        cert_pem = open(cert_path, "rb").read()
        key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
        return {"cert_pem": cert_pem, "key": key,
                "cert": x509.load_pem_x509_certificate(cert_pem)}
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "korgex dev capture CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                key_encipherment=False, content_commitment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            # Subject Key Identifier — strict verifiers (OpenSSL 3.x on py3.13+) need it
            # on the CA so a leaf's Authority Key Identifier can chain to it.
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    with open(cert_path, "wb") as f:
        f.write(cert_pem)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    os.chmod(key_path, 0o600)
    return {"cert_pem": cert_pem, "key": key, "cert": cert}


def leaf_cert(host: str, ca: dict | None = None):
    """Return ``(leaf_cert_pem, leaf_key_pem)`` for ``host``, signed by the CA."""
    ca = ca or ensure_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca["cert"].subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            # Authority Key Identifier chains the leaf to the CA — required by strict
            # OpenSSL (py3.13+) to build/verify the path ("Missing Authority Key Id").
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca["key"].public_key()),
                critical=False)
            .sign(ca["key"], hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()))


# ── redaction (secrets never leave this module) ──────────────────────────────
def _redact_headers(headers: dict) -> dict:
    out = {}
    for k, v in headers.items():
        out[k] = "[REDACTED]" if k.lower() in _SECRET_HEADERS else _redact(v)
    return out


def _ci_get(headers: dict, name: str) -> str:
    """Case-insensitive header lookup (servers vary on Content-Encoding casing)."""
    name = name.lower()
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return ""


def _decode_body(raw: bytes, encoding: str) -> str:
    if not raw:
        return ""
    enc = (encoding or "").lower().strip()
    try:
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc in ("deflate", "zlib"):
            raw = zlib.decompress(raw)
        elif enc == "br":  # brotli — not stdlib; present iff the app could request it
            try:
                import brotli
                raw = brotli.decompress(raw)
            except ImportError:
                return f"[{len(raw)} bytes, brotli-compressed — `pip install brotli` to decode]"
        elif enc == "zstd":
            try:
                import zstandard
                raw = zstandard.ZstdDecompressor().decompress(raw)
            except ImportError:
                return f"[{len(raw)} bytes, zstd-compressed]"
    except Exception:  # noqa: BLE001 — a decode failure must not lose the record
        return f"[{len(raw)} bytes, undecodable {enc or 'identity'} body]"
    return _redact(raw[:_MAX_BODY].decode("utf-8", "replace"))


def _capture_record(method, url, req_headers, req_body, status, resp_headers, resp_body, ms):
    return {
        "method": method, "url": url, "status": status, "ms": ms,
        "req_headers": _redact_headers(req_headers),
        "req_body": _decode_body(req_body, _ci_get(req_headers, "Content-Encoding")),
        "resp_headers": _redact_headers(resp_headers),
        "resp_body": _decode_body(resp_body, _ci_get(resp_headers, "Content-Encoding")),
    }


# ── the capture proxy ────────────────────────────────────────────────────────
_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")


def _read_body(handler) -> bytes:
    length = handler.headers.get("Content-Length")
    if length and length.isdigit():
        return handler.rfile.read(int(length))
    return b""


def _forward(scheme, host, port, method, path, headers, body, timeout=30):
    """Forward one request upstream and return (status, resp_headers, resp_body)."""
    conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    kw = {"timeout": timeout}
    if scheme == "https":
        # We are the client to the REAL server here; default verification applies.
        kw["context"] = ssl.create_default_context()
    conn = conn_cls(host, port, **kw)
    fwd = {k: v for k, v in headers.items() if k.lower() not in ("proxy-connection",)}
    fwd["Connection"] = "close"
    conn.request(method, path, body=body or None, headers=fwd)
    r = conn.getresponse()
    resp_headers = dict(r.getheaders())
    resp_body = r.read()
    conn.close()
    return r.status, resp_headers, resp_body


def _proxy_once(handler, scheme, host, port, path) -> None:
    """Capture + forward ONE request on ``handler``, write the response back."""
    body = _read_body(handler)
    req_headers = dict(handler.headers.items())
    url = f"{scheme}://{host}{'' if port in (80, 443) else ':' + str(port)}{path}"
    t0 = time.monotonic()
    try:
        status, resp_headers, resp_body = _forward(scheme, host, port, handler.command,
                                                   path, req_headers, body)
    except Exception as e:  # noqa: BLE001 — upstream failure becomes a 502 + a record
        status, resp_headers, resp_body = 502, {}, f"korgex netcapture upstream error: {e}".encode()
    ms = int((time.monotonic() - t0) * 1000)
    handler.server._captures.append(
        _capture_record(handler.command, url, req_headers, body, status, resp_headers, resp_body, ms))
    # relay the (unmodified) response to the client
    handler.send_response_only(status)
    for k, v in resp_headers.items():
        if k.lower() in ("transfer-encoding", "connection", "content-length"):
            continue
        handler.send_header(k, v)
    handler.send_header("Content-Length", str(len(resp_body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(resp_body)


class _TunnelHandler(http.server.BaseHTTPRequestHandler):
    """Handler for the DECRYPTED tunnel — every method forwards to the pinned host."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silence
        pass

    def _do(self):
        _proxy_once(self, "https", self.server._target_host, self.server._target_port, self.path)
    for _m in _METHODS:
        locals()[f"do_{_m}"] = _do


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Top-level proxy: plaintext HTTP forwards directly; CONNECT starts a TLS tunnel."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_CONNECT(self):
        host, _, port = self.path.partition(":")
        port = int(port or 443)
        self.send_response(200, "Connection established")
        self.end_headers()
        cert_pem, key_pem = self.server._leaf(host)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        with tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False) as cf, \
                tempfile.NamedTemporaryFile("wb", suffix=".key", delete=False) as kf:
            cf.write(cert_pem)
            kf.write(key_pem)
            cert_file, key_file = cf.name, kf.name
        try:
            ctx.load_cert_chain(cert_file, key_file)
            tls = ctx.wrap_socket(self.connection, server_side=True)
        except (ssl.SSLError, OSError):
            return
        finally:
            os.unlink(cert_file)
            os.unlink(key_file)
        # serve decrypted requests on a per-tunnel server that pins the target host
        inner = _TunnelServer((host, port), self.server._captures)
        try:
            _TunnelHandler(tls, self.client_address, inner)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _do(self):
        # Plaintext: self.path is a full URL (http://host:port/path).
        parts = self.path.split("/", 3)
        hostport = parts[2] if len(parts) > 2 else ""
        host, _, port = hostport.partition(":")
        path = "/" + (parts[3] if len(parts) > 3 else "")
        _proxy_once(self, "http", host, int(port or 80), path)
    for _m in _METHODS:
        locals()[f"do_{_m}"] = _do


class _TunnelServer:
    """A lightweight stand-in 'server' the _TunnelHandler reads target + captures off of."""
    def __init__(self, target, captures):
        self._target_host, self._target_port = target
        self._captures = captures


class CaptureProxy(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded capture proxy. ``captures`` accumulates redacted exchange records."""
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self._captures: list = []
        self._ca = ensure_ca()
        self._leaf_cache: dict = {}
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return self.server_address[1]

    def _leaf(self, host: str):
        with self._lock:
            if host not in self._leaf_cache:
                self._leaf_cache[host] = leaf_cert(host, self._ca)
            return self._leaf_cache[host]


# ── run an app under capture ─────────────────────────────────────────────────
def run_with_capture(command: list, cwd: str | None = None, timeout: int = 120) -> dict:
    """Run ``command`` behind the capture proxy (process-scoped) and return
    ``{exit_code, stdout, stderr, captures, count}``. NEVER raises — a setup or run
    failure comes back as an error field so the agent loop keeps going."""
    try:
        proxy = CaptureProxy()
    except OSError as e:
        return {"error": f"could not start capture proxy: {e}", "captures": [], "count": 0}
    t = threading.Thread(target=proxy.serve_forever, daemon=True)
    t.start()
    ca_path = os.path.join(_capture_dir(), "ca.pem")
    proxy_url = f"http://127.0.0.1:{proxy.port}"
    env = dict(os.environ)
    env.update({
        "HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url, "https_proxy": proxy_url,
        # trust the capture CA — for THIS subprocess only (common client knobs)
        "REQUESTS_CA_BUNDLE": ca_path, "SSL_CERT_FILE": ca_path,
        "CURL_CA_BUNDLE": ca_path, "NODE_EXTRA_CA_CERTS": ca_path,
        "NO_PROXY": "", "no_proxy": "",
    })
    try:
        proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
        out = {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired as e:
        out = {"exit_code": None, "error": f"command timed out after {timeout}s",
               "stdout": e.stdout or "", "stderr": e.stderr or ""}
    except (OSError, ValueError) as e:
        out = {"exit_code": None, "error": f"could not run command: {e}",
               "stdout": "", "stderr": ""}
    finally:
        proxy.shutdown()
        proxy.server_close()
    out["captures"] = list(proxy._captures)
    out["count"] = len(proxy._captures)
    return out
