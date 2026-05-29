"""
google_auth.py — connect a user's Google account to korgex via OAuth 2.0 (PKCE),
with a Gemini API-key fallback. "OAuth first, then API."

Flow (desktop/CLI, PKCE — no client secret required for a public client):
  1. generate_pkce() → verifier + S256 challenge
  2. build_auth_url(...) → open in the browser; user consents with their Google account
  3. a local callback server (make_callback_server / capture_redirect) catches ?code=
  4. build_token_request(...) → POST to TOKEN_ENDPOINT → parse_token_response → TokenStore.save
  5. resolve_credentials() hands korgex a {base_url, api_key} for Gemini — using the
     OAuth access token (refreshed as needed) or a GEMINI_API_KEY fallback.

Live Gemini calls then go through korgex's existing OpenAI-compatible path
(base_url = Google's OpenAI-compat endpoint, bearer = the resolved credential).

Setup the integrator does ONCE: register a Google Cloud OAuth client (Desktop
app), set GOOGLE_OAUTH_CLIENT_ID, and add the loopback redirect URI. The
client_id is runtime config — none of this module hardcodes it.

Note: a consumer "Gemini Advanced" subscription is NOT programmatically usable by
a third-party app; this authorizes the Gemini API under the user's Google account.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# Google's OpenAI-compatible endpoint for Gemini — lets korgex's openai path drive it.
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Override via GOOGLE_OAUTH_SCOPES (space-separated) to match the scopes granted
# to your registered client.
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/generative-language.retriever"]

_EXPIRY_SKEW_SECS = 60  # refresh a bit early


def generate_pkce() -> tuple:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _scopes(scopes=None) -> list:
    if scopes:
        return scopes
    env = os.environ.get("GOOGLE_OAUTH_SCOPES")
    return env.split() if env else list(DEFAULT_SCOPES)


def build_auth_url(client_id: str, redirect_uri: str, code_challenge: str,
                   scopes=None, state: str = None) -> str:
    """Build the Google authorization URL. access_type=offline + prompt=consent
    so Google returns a refresh_token."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(_scopes(scopes)),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return AUTH_ENDPOINT + "?" + urlencode(params)


def build_token_request(code: str, code_verifier: str, client_id: str,
                        redirect_uri: str, client_secret: str = None) -> dict:
    """POST body for exchanging an auth code for tokens."""
    d = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        d["client_secret"] = client_secret
    return d


def build_refresh_request(refresh_token: str, client_id: str,
                          client_secret: str = None) -> dict:
    """POST body for refreshing an access token."""
    d = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        d["client_secret"] = client_secret
    return d


def parse_token_response(resp: dict, now: float) -> dict:
    """Normalize a Google token response into a storable token dict."""
    return {
        "access_token": resp.get("access_token"),
        "refresh_token": resp.get("refresh_token"),
        "expires_at": now + float(resp.get("expires_in", 3600)),
        "token_type": resp.get("token_type", "Bearer"),
        "scope": resp.get("scope"),
    }


class TokenStore:
    """Persists the Google token at ~/.korgex/google_token.json (0600)."""

    def __init__(self, path: str = None) -> None:
        self.path = Path(path or os.path.join(
            os.path.expanduser("~"), ".korgex", "google_token.json"))

    def save(self, token: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(token, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def load(self):
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, ValueError, OSError):
            return None

    @staticmethod
    def is_expired(token: dict, now: float, skew: float = _EXPIRY_SKEW_SECS) -> bool:
        return now >= (float(token.get("expires_at", 0)) - skew)


def resolve_credentials(now: float, token_store: "TokenStore" = None) -> dict:
    """Resolve Gemini credentials for korgex's OpenAI-compatible path.

    Prefers a non-expired OAuth access token; else GEMINI_API_KEY / GOOGLE_API_KEY.
    Returns {base_url, api_key, source} or None if nothing is configured.
    (Refreshing an expired OAuth token is the caller's job — needs the network.)
    """
    store = token_store or TokenStore()
    tok = store.load()
    if tok and tok.get("access_token") and not TokenStore.is_expired(tok, now):
        return {"base_url": GEMINI_OPENAI_BASE, "api_key": tok["access_token"], "source": "oauth"}
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return {"base_url": GEMINI_OPENAI_BASE, "api_key": key, "source": "api_key"}
    return None


# ── local loopback callback server (catches the OAuth redirect) ───────────

class _CallbackHandler(BaseHTTPRequestHandler):
    captured: dict = {}

    def do_GET(self):  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        type(self).captured = {
            "code": (q.get("code") or [None])[0],
            "state": (q.get("state") or [None])[0],
            "error": (q.get("error") or [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h3>korgex is connected to Google. You can close this tab.</h3>")

    def log_message(self, *args):  # silence the default stderr logging
        pass


def make_callback_server(port: int = 0):
    """Bind a loopback callback server. Returns (httpd, port). port=0 → OS-assigned."""
    httpd = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    return httpd, httpd.server_address[1]


def capture_redirect(httpd, timeout: float = 120.0) -> dict:
    """Handle exactly one redirect request and return {code, state, error}."""
    httpd.timeout = timeout
    _CallbackHandler.captured = {}
    httpd.handle_request()
    return dict(_CallbackHandler.captured)


# ── runtime glue (network/browser — exercised live, not in unit tests) ────

def connect(client_id: str, scopes=None, client_secret: str = None,
            token_store: "TokenStore" = None, port: int = 0,
            open_browser: bool = True) -> dict:  # pragma: no cover
    """Run the full interactive OAuth flow and persist the token. Returns it."""
    import time
    import webbrowser
    import requests

    store = token_store or TokenStore()
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    httpd, port = make_callback_server(port)
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    url = build_auth_url(client_id, redirect_uri, challenge, scopes=scopes, state=state)

    print("\nConnect your Google account — open this URL in your browser:\n  " + url + "\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    cb = capture_redirect(httpd, timeout=300)
    if cb.get("error") or not cb.get("code"):
        raise RuntimeError(f"OAuth failed: {cb.get('error') or 'no authorization code'}")
    if state and cb.get("state") != state:
        raise RuntimeError("OAuth state mismatch — possible CSRF, aborting")

    resp = requests.post(TOKEN_ENDPOINT, timeout=30, data=build_token_request(
        cb["code"], verifier, client_id, redirect_uri, client_secret))
    resp.raise_for_status()
    tok = parse_token_response(resp.json(), now=time.time())
    store.save(tok)
    return tok


def refresh_if_needed(client_id: str, token_store: "TokenStore" = None,
                      client_secret: str = None, now: float = None):  # pragma: no cover
    """Refresh the stored access token if it's expired. Returns the live token."""
    import time
    import requests

    store = token_store or TokenStore()
    tok = store.load()
    if not tok:
        return None
    now = time.time() if now is None else now
    if not TokenStore.is_expired(tok, now):
        return tok
    rt = tok.get("refresh_token")
    if not rt:
        return None
    resp = requests.post(TOKEN_ENDPOINT, timeout=30,
                         data=build_refresh_request(rt, client_id, client_secret))
    resp.raise_for_status()
    new = parse_token_response(resp.json(), now=now)
    if not new.get("refresh_token"):
        new["refresh_token"] = rt  # Google often omits it on refresh — keep the old one
    store.save(new)
    return new


def main():  # pragma: no cover — `python -m src.google_auth connect|refresh`
    import sys
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    if not cid:
        print("Set GOOGLE_OAUTH_CLIENT_ID first (register a 'Desktop app' OAuth client in\n"
              "Google Cloud Console → APIs & Services → Credentials; add http://127.0.0.1 as a\n"
              "redirect). Optionally GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_OAUTH_SCOPES.")
        return 2
    secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "connect"
    if cmd == "connect":
        connect(cid, client_secret=secret)
        print("✓ Connected — Google token stored at ~/.korgex/google_token.json")
    elif cmd == "refresh":
        print("✓ Refreshed" if refresh_if_needed(cid, client_secret=secret) else "nothing to refresh")
    else:
        print(f"unknown command: {cmd} (use: connect | refresh)")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
