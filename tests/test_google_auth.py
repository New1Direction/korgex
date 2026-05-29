"""
Google OAuth (for the Gemini API) + API-key fallback — tests.

"Connect your Google account" via OAuth 2.0 + PKCE, with a pasted Gemini API key
as fallback. Everything here is offline-testable: PKCE, the auth URL, the token
exchange/refresh request shapes, token parsing/storage/expiry, credential
resolution (OAuth token preferred, else GEMINI_API_KEY), and a real localhost
callback-capture roundtrip. The Google client_id is runtime config; live Gemini
calls go through korgex's existing OpenAI-compatible path.
"""

import base64
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import google_auth as G  # noqa: E402


# ── PKCE ──────────────────────────────────────────────────────────────────

def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = G.generate_pkce()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge   # base64url, unpadded


# ── auth URL ──────────────────────────────────────────────────────────────

def test_build_auth_url_has_required_params():
    url = G.build_auth_url("client123.apps.googleusercontent.com",
                           "http://127.0.0.1:8765/callback",
                           code_challenge="abc", state="xyz")
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(url).query)
    assert url.startswith(G.AUTH_ENDPOINT)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["client123.apps.googleusercontent.com"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8765/callback"]
    assert q["code_challenge"] == ["abc"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["xyz"]
    assert q["access_type"] == ["offline"]      # so we get a refresh_token
    assert "scope" in q


# ── token exchange / refresh request shapes ───────────────────────────────

def test_token_request_is_auth_code_grant():
    d = G.build_token_request(code="C", code_verifier="V",
                              client_id="ID", redirect_uri="R", client_secret="S")
    assert d["grant_type"] == "authorization_code"
    assert d["code"] == "C" and d["code_verifier"] == "V"
    assert d["client_id"] == "ID" and d["redirect_uri"] == "R" and d["client_secret"] == "S"


def test_refresh_request_is_refresh_grant():
    d = G.build_refresh_request(refresh_token="RT", client_id="ID")
    assert d["grant_type"] == "refresh_token"
    assert d["refresh_token"] == "RT" and d["client_id"] == "ID"


def test_parse_token_response_computes_expiry_and_keeps_refresh():
    tok = G.parse_token_response(
        {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "token_type": "Bearer"},
        now=1000.0)
    assert tok["access_token"] == "AT" and tok["refresh_token"] == "RT"
    assert tok["expires_at"] == 4600.0


# ── token store + expiry ───────────────────────────────────────────────────

def test_token_store_roundtrip_and_expiry(tmp_path):
    store = G.TokenStore(str(tmp_path / "google_token.json"))
    assert store.load() is None
    tok = {"access_token": "AT", "refresh_token": "RT", "expires_at": 5000.0}
    store.save(tok)
    assert store.load() == tok
    assert G.TokenStore.is_expired(tok, now=4900.0) is False   # before expiry (minus skew)
    assert G.TokenStore.is_expired(tok, now=5001.0) is True


# ── credential resolution (OAuth preferred, key fallback) ─────────────────

def test_resolve_prefers_valid_oauth_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    store = G.TokenStore(str(tmp_path / "t.json"))
    store.save({"access_token": "OAUTH_AT", "expires_at": 9999999999.0})
    creds = G.resolve_credentials(now=1000.0, token_store=store)
    assert creds["source"] == "oauth"
    assert creds["api_key"] == "OAUTH_AT"
    assert creds["base_url"] == G.GEMINI_OPENAI_BASE


def test_resolve_falls_back_to_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-key")
    store = G.TokenStore(str(tmp_path / "t.json"))  # empty
    creds = G.resolve_credentials(now=1000.0, token_store=store)
    assert creds["source"] == "api_key"
    assert creds["api_key"] == "AIza-key"


def test_resolve_none_when_nothing_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    store = G.TokenStore(str(tmp_path / "t.json"))
    assert G.resolve_credentials(now=1000.0, token_store=store) is None


def test_resolve_ignores_expired_oauth_and_uses_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-key")
    store = G.TokenStore(str(tmp_path / "t.json"))
    store.save({"access_token": "STALE", "expires_at": 10.0})   # expired
    creds = G.resolve_credentials(now=1000.0, token_store=store)
    assert creds["source"] == "api_key"   # expired OAuth skipped


# ── local callback server (real localhost roundtrip) ──────────────────────

def test_callback_server_captures_code_and_state():
    httpd, port = G.make_callback_server(0)
    out = {}

    def serve():
        out.update(G.capture_redirect(httpd, timeout=10))

    t = threading.Thread(target=serve)
    t.start()
    time.sleep(0.15)
    urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=AUTHCODE&state=ST", timeout=5).read()
    t.join(timeout=5)
    assert out["code"] == "AUTHCODE"
    assert out["state"] == "ST"
