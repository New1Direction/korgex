"""Authorized remote signer RPC client.

This deliberately models a signer we own/control, not process injection into a third-party
mobile app. The agent may call a constrained HTTP signer, but the client must fail closed:
allowlisted hosts only, bearer auth required, hex inputs validated, and returned signatures
verified before the agent trusts them.
"""
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

import pytest

from src import remote_signer as RS
from src import signing as SG


class _SigningHandler(BaseHTTPRequestHandler):
    priv_hex: str = ""
    token = "test-token"
    seen: list[dict[str, Optional[str]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(n).decode("utf-8")
        self.__class__.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self.path != "/sign":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')
            return
        req = json.loads(body)
        tip = req["tip"]
        pub = SG.public_of(self.priv_hex)
        sig = SG.sign_tip(self.priv_hex, tip)
        out = json.dumps({"pubkey": pub, "tip": tip, "sig": sig}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def signer_server():
    priv, pub = SG.generate_keypair()
    _SigningHandler.priv_hex = priv
    _SigningHandler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _SigningHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/sign", pub, _SigningHandler.seen
    finally:
        httpd.shutdown()
        thread.join(timeout=3)


def test_remote_signer_posts_tip_with_bearer_auth_and_verifies_result(signer_server):
    url, expected_pub, seen = signer_server
    tip = "ab" * 32

    cp = RS.sign_tip_via_http(url, tip, bearer_token="test-token", allowed_hosts=["127.0.0.1"])

    assert cp == {"pubkey": expected_pub, "tip": tip, "sig": cp["sig"]}
    assert SG.verify_tip(cp["pubkey"], cp["tip"], cp["sig"])
    assert seen[0]["auth"] == "Bearer test-token"
    assert json.loads(seen[0]["body"]) == {"tip": tip}


def test_remote_signer_rejects_hosts_not_explicitly_allowlisted(signer_server):
    url, _, _ = signer_server

    with pytest.raises(RS.RemoteSignerError, match="not in allowlist"):
        RS.sign_tip_via_http(url, "ab" * 32, bearer_token="test-token", allowed_hosts=["example.com"])


def test_remote_signer_requires_bearer_token(signer_server):
    url, _, _ = signer_server

    with pytest.raises(RS.RemoteSignerError, match="bearer token"):
        RS.sign_tip_via_http(url, "ab" * 32, bearer_token="", allowed_hosts=["127.0.0.1"])


def test_remote_signer_rejects_non_hex_or_wrong_length_tips(signer_server):
    url, _, _ = signer_server

    with pytest.raises(RS.RemoteSignerError, match="32-byte hex"):
        RS.sign_tip_via_http(url, "not-hex", bearer_token="test-token", allowed_hosts=["127.0.0.1"])


def test_remote_signer_rejects_unverifiable_signatures(monkeypatch, signer_server):
    url, _, _ = signer_server
    _, pub = SG.generate_keypair()

    def fake_post_json(*args, **kwargs):
        return {"pubkey": pub, "tip": "ab" * 32, "sig": "00" * 64}

    monkeypatch.setattr(RS, "_post_json", fake_post_json)
    with pytest.raises(RS.RemoteSignerError, match="did not verify"):
        RS.sign_tip_via_http(url, "ab" * 32, bearer_token="test-token", allowed_hosts=["127.0.0.1"])


def test_remote_sign_tool_reads_secret_from_env_and_uses_allowed_hosts(monkeypatch):
    from src.tools_impl import tool_remote_sign_tip

    seen = {}

    def fake_sign(url, tip_hex, *, bearer_token, allowed_hosts,
                  expected_pubkeys=None, require_https=False, timeout=10.0):
        seen.update({
            "url": url,
            "tip_hex": tip_hex,
            "bearer_token": bearer_token,
            "allowed_hosts": list(allowed_hosts),
            "expected_pubkeys": list(expected_pubkeys) if expected_pubkeys else None,
            "require_https": require_https,
            "timeout": timeout,
        })
        return {"pubkey": "11" * 32, "tip": tip_hex, "sig": "22" * 64}

    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_TOKEN", "env-token")
    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_ALLOWED_HOSTS", "127.0.0.1,phone.local")
    monkeypatch.delenv("KORGEX_REMOTE_SIGNER_PUBKEY", raising=False)
    monkeypatch.delenv("KORGEX_REMOTE_SIGNER_REQUIRE_HTTPS", raising=False)
    monkeypatch.setattr(RS, "sign_tip_via_http", fake_sign)

    out = tool_remote_sign_tip("http://127.0.0.1:8080/sign", "ab" * 32)

    assert out["checkpoint"]["pubkey"] == "11" * 32
    assert seen == {
        "url": "http://127.0.0.1:8080/sign",
        "tip_hex": "ab" * 32,
        "bearer_token": "env-token",
        "allowed_hosts": ["127.0.0.1", "phone.local"],
        "expected_pubkeys": None,
        "require_https": False,
        "timeout": 10.0,
    }


def test_remote_sign_tool_fails_closed_without_token(monkeypatch):
    from src.tools_impl import tool_remote_sign_tip

    monkeypatch.delenv("KORGEX_REMOTE_SIGNER_TOKEN", raising=False)
    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_ALLOWED_HOSTS", "127.0.0.1")

    out = tool_remote_sign_tip("http://127.0.0.1:8080/sign", "ab" * 32)

    assert "error" in out and "KORGEX_REMOTE_SIGNER_TOKEN" in out["error"]


def test_remote_sign_tool_is_routable_from_agent_tool_layer(monkeypatch):
    from src import tool_abstraction as TA

    seen = {}

    def fake_tool(url, tip_hex, context=None):
        assert context is not None
        seen.update({"url": url, "tip_hex": tip_hex, "repo_root": context["repo_root"]})
        return {"ok": True, "checkpoint": {"tip": tip_hex}}

    monkeypatch.setattr("src.tools_impl.tool_remote_sign_tip", fake_tool)

    out = TA.route_tool_call(
        "RemoteSignTip",
        {"url": "http://127.0.0.1:8080/sign", "tip_hex": "ab" * 32},
        repo_root="/tmp/repo",
    )

    assert out == {"ok": True, "checkpoint": {"tip": "ab" * 32}}
    assert seen == {"url": "http://127.0.0.1:8080/sign", "tip_hex": "ab" * 32, "repo_root": "/tmp/repo"}


# ── HIGH: pin the signer identity (local verify alone is self-referential) ─────

def test_remote_signer_rejects_valid_sig_from_unpinned_key(signer_server):
    """The signer returns a *valid* sig — but under a key we never authorized. Reject it.

    This is the hole the bare local-verify leaves open: verify_tip(pubkey, tip, sig) proves
    the sig matches the *returned* key, not that it's the key we trust.
    """
    url, _server_pub, _ = signer_server
    _, other_pub = SG.generate_keypair()
    with pytest.raises(RS.RemoteSignerError, match="pinned"):
        RS.sign_tip_via_http(
            url, "ab" * 32, bearer_token="test-token",
            allowed_hosts=["127.0.0.1"], expected_pubkeys=[other_pub],
        )


def test_remote_signer_accepts_pinned_key(signer_server):
    url, server_pub, _ = signer_server
    cp = RS.sign_tip_via_http(
        url, "ab" * 32, bearer_token="test-token",
        allowed_hosts=["127.0.0.1"], expected_pubkeys=[server_pub],
    )
    assert cp["pubkey"] == server_pub
    assert SG.verify_tip(cp["pubkey"], cp["tip"], cp["sig"])


def test_remote_signer_rejects_tip_substitution(monkeypatch, signer_server):
    """The returned-tip != requested-tip guard — previously uncovered."""
    url, _, _ = signer_server

    def fake_post_json(*a, **k):
        priv, pub = SG.generate_keypair()
        other = "cd" * 32
        return {"pubkey": pub, "tip": other, "sig": SG.sign_tip(priv, other)}

    monkeypatch.setattr(RS, "_post_json", fake_post_json)
    with pytest.raises(RS.RemoteSignerError, match="different tip"):
        RS.sign_tip_via_http(url, "ab" * 32, bearer_token="t", allowed_hosts=["127.0.0.1"])


# ── MEDIUM: redirects stay on-allowlist and never carry the bearer token ───────

def test_redirect_handler_rejects_offallowlist_target():
    h = RS._AllowlistRedirectHandler({"signer.local"})
    req = urllib.request.Request("http://signer.local/sign", headers={"Authorization": "Bearer x"})
    with pytest.raises(RS.RemoteSignerError, match="off-allowlist"):
        h.redirect_request(req, None, 302, "Found", {}, "http://evil.example/")


def test_redirect_handler_strips_token_on_host_change():
    h = RS._AllowlistRedirectHandler({"a.local", "b.local"})
    req = urllib.request.Request("http://a.local/sign", headers={"Authorization": "Bearer x"})
    new = h.redirect_request(req, None, 302, "Found", {}, "http://b.local/sign")
    assert new is not None
    assert all(k.lower() != "authorization" for k in new.headers)


def test_redirect_handler_keeps_token_same_host():
    h = RS._AllowlistRedirectHandler({"a.local"})
    req = urllib.request.Request("http://a.local/sign", headers={"Authorization": "Bearer x"})
    new = h.redirect_request(req, None, 302, "Found", {}, "http://a.local/elsewhere")
    assert new is not None
    assert any(k.lower() == "authorization" for k in new.headers)


class _RedirectingHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self):
        self.send_response(302)
        self.send_header("Location", "http://evil.example/")
        self.end_headers()


@pytest.fixture
def redirecting_server():
    httpd = HTTPServer(("127.0.0.1", 0), _RedirectingHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/sign"
    finally:
        httpd.shutdown()
        thread.join(timeout=3)


def test_remote_signer_blocks_offallowlist_redirect_end_to_end(redirecting_server):
    # The signer 302s to an off-allowlist host; the token must never follow it there.
    with pytest.raises(RS.RemoteSignerError, match="off-allowlist"):
        RS.sign_tip_via_http(
            redirecting_server, "ab" * 32, bearer_token="t", allowed_hosts=["127.0.0.1"],
        )


# ── MEDIUM: transport — http preserved by default, https enforceable + warned ──

def test_require_https_blocks_plaintext_to_remote_host():
    with pytest.raises(RS.RemoteSignerError, match="https"):
        RS.sign_tip_via_http(
            "http://signer.example/sign", "ab" * 32, bearer_token="t",
            allowed_hosts=["signer.example"], require_https=True,
        )


def test_require_https_still_allows_loopback_plaintext(signer_server):
    url, server_pub, _ = signer_server  # http://127.0.0.1:.../sign
    cp = RS.sign_tip_via_http(
        url, "ab" * 32, bearer_token="test-token",
        allowed_hosts=["127.0.0.1"], require_https=True,
    )
    assert cp["pubkey"] == server_pub


def test_is_plaintext_remote_classifies_transport():
    assert RS.is_plaintext_remote("http://phone.lan/sign") is True
    assert RS.is_plaintext_remote("http://127.0.0.1:8/sign") is False
    assert RS.is_plaintext_remote("http://localhost:8/sign") is False
    assert RS.is_plaintext_remote("https://phone.lan/sign") is False


# ── handler wiring for the new optional env knobs ─────────────────────────────

def test_remote_sign_tool_pins_pubkey_and_requires_https_from_env(monkeypatch):
    from src.tools_impl import tool_remote_sign_tip

    seen = {}

    def fake_sign(url, tip_hex, *, bearer_token, allowed_hosts,
                  expected_pubkeys=None, require_https=False, timeout=10.0):
        seen["expected_pubkeys"] = list(expected_pubkeys) if expected_pubkeys else None
        seen["require_https"] = require_https
        return {"pubkey": "11" * 32, "tip": tip_hex, "sig": "22" * 64}

    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_TOKEN", "t")
    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_PUBKEY", "aa" * 32 + "," + "bb" * 32)
    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_REQUIRE_HTTPS", "1")
    monkeypatch.setattr(RS, "sign_tip_via_http", fake_sign)

    out = tool_remote_sign_tip("http://127.0.0.1:8080/sign", "ab" * 32)

    assert out["ok"] is True
    assert seen["expected_pubkeys"] == ["aa" * 32, "bb" * 32]
    assert seen["require_https"] is True
    assert "warnings" not in out   # pinned + loopback url → nothing to warn about


def test_remote_sign_tool_warns_when_unpinned_and_plaintext(monkeypatch):
    from src.tools_impl import tool_remote_sign_tip

    def fake_sign(url, tip_hex, *, bearer_token, allowed_hosts,
                  expected_pubkeys=None, require_https=False, timeout=10.0):
        return {"pubkey": "11" * 32, "tip": tip_hex, "sig": "22" * 64}

    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_TOKEN", "t")
    monkeypatch.setenv("KORGEX_REMOTE_SIGNER_ALLOWED_HOSTS", "phone.lan")
    monkeypatch.delenv("KORGEX_REMOTE_SIGNER_PUBKEY", raising=False)
    monkeypatch.delenv("KORGEX_REMOTE_SIGNER_REQUIRE_HTTPS", raising=False)
    monkeypatch.setattr(RS, "sign_tip_via_http", fake_sign)

    out = tool_remote_sign_tip("http://phone.lan/sign", "ab" * 32)

    assert out["ok"] is True
    warns = " ".join(out.get("warnings", []))
    assert "KORGEX_REMOTE_SIGNER_PUBKEY" in warns   # identity-not-pinned warning
    assert "plaintext" in warns                      # transport warning
