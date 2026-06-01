"""OAuth for remote (HTTP) MCP servers.

korgex could only auth remote servers with a static ``Authorization: Bearer ${ENV}``
header. This adds the OAuth path so a server like Linear/Sentry "just works" after
one `korgex mcp login <server>`:

  * a **token store** (~/.korgex/mcp_tokens.json, 0600) per server,
  * **auto-refresh** when the access token nears expiry (using the refresh token),
  * the token **auto-applied as a Bearer header** when that server connects,
  * a **login flow** (discover → register → browser authorize w/ PKCE → exchange).

The store, expiry/refresh logic, PKCE, and metadata parsing are pure + tested. The
browser+localhost-callback leg of `login()` runs live (can't be unit-tested
headless) but is built on the same tested pieces.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass


@dataclass
class Token:
    access_token: str
    refresh_token: str | None = None
    expires_at: float = 0.0   # epoch seconds; 0 = unknown / never expires
    token_url: str | None = None      # for refresh
    client_id: str | None = None      # for refresh


def _default_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".korgex", "mcp_tokens.json")


class TokenStore:
    """Per-server OAuth tokens, persisted with 0600 perms."""

    def __init__(self, path: str = None):
        self.path = path or _default_path()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path) as f:
                self._data = {k: Token(**v) for k, v in json.load(f).items()}
        except (FileNotFoundError, ValueError, OSError, TypeError):
            self._data = {}

    def _save(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({k: asdict(v) for k, v in self._data.items()}, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def get(self, server: str):
        return self._data.get(server)

    def set(self, server: str, token: Token) -> None:
        self._data[server] = token
        self._save()


def is_expired(token: Token, now: float, skew: int = 120) -> bool:
    """True if the token expires within `skew` seconds (0 expiry = never)."""
    return bool(token.expires_at) and now >= token.expires_at - skew


def valid_token(store: TokenStore, server: str, now: float, refresher=None):
    """A usable access token for `server`, refreshing if it's near expiry and a
    refresh token + refresher are available. None if there's no token at all."""
    tok = store.get(server)
    if tok is None:
        return None
    if is_expired(tok, now) and tok.refresh_token and refresher:
        try:
            new = refresher(tok.refresh_token)
        except Exception:
            new = None
        if new:
            store.set(server, new)
            tok = new
    return tok.access_token


def auth_header(store: TokenStore, server: str, now: float, refresher=None) -> dict:
    """``{"Authorization": "Bearer …"}`` for a server with a stored token, else {}."""
    t = valid_token(store, server, now, refresher)
    return {"Authorization": f"Bearer {t}"} if t else {}


# ── PKCE + metadata (pure helpers used by the login flow) ─────────────────────

def pkce_pair():
    """Return (verifier, challenge, method) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge, "S256"


def parse_as_metadata(doc: dict) -> dict:
    """Pull the OAuth endpoints out of an authorization-server metadata document."""
    return {
        "authorization_endpoint": doc.get("authorization_endpoint", ""),
        "token_endpoint": doc.get("token_endpoint", ""),
        "registration_endpoint": doc.get("registration_endpoint", ""),
    }


def apply_stored_tokens(configs: dict, store: TokenStore = None, now: float = None) -> dict:
    """For every HTTP server config that has a stored token (and no explicit auth
    header), inject ``Authorization: Bearer <token>``. Returns configs (mutated)."""
    import time
    store = store if store is not None else TokenStore()
    now = time.time() if now is None else now
    for name, cfg in configs.items():
        if getattr(cfg, "transport", "") != "http":
            continue
        if any(k.lower() == "authorization" for k in (cfg.headers or {})):
            continue  # respect an explicit token
        hdr = auth_header(store, name, now, refresher=_make_refresher(store, name))
        if hdr:
            cfg.headers = {**(cfg.headers or {}), **hdr}
    return configs


def _make_refresher(store: TokenStore, server: str):
    """A refresher(refresh_token)->Token that hits the stored token endpoint."""
    tok = store.get(server)
    if not tok or not tok.token_url or not tok.client_id:
        return None

    def refresh(refresh_token: str):
        import time

        import httpx
        r = httpx.post(tok.token_url, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": tok.client_id}, timeout=30)
        if r.status_code >= 400:
            return None
        d = r.json()
        return Token(access_token=d["access_token"],
                     refresh_token=d.get("refresh_token", refresh_token),
                     expires_at=time.time() + int(d.get("expires_in", 3600)),
                     token_url=tok.token_url, client_id=tok.client_id)
    return refresh


def login(server: str, url: str, store: TokenStore = None) -> dict:  # pragma: no cover — live browser leg
    """Run the OAuth authorization-code + PKCE flow for a remote MCP server and store
    the token. Opens the browser and catches the redirect on a localhost callback —
    so this runs interactively, not in tests. Built on the tested pieces above.
    """
    import http.server
    import threading
    import time
    import urllib.parse
    import webbrowser

    import httpx

    store = store if store is not None else TokenStore()
    base = url.rsplit("/mcp", 1)[0].rstrip("/")
    # 1. discover the authorization server metadata (best-effort across well-knowns)
    md = {}
    for wk in (f"{base}/.well-known/oauth-authorization-server",
               f"{url.rstrip('/')}/.well-known/oauth-authorization-server",
               f"{base}/.well-known/openid-configuration"):
        try:
            r = httpx.get(wk, timeout=15)
            if r.status_code < 400:
                md = parse_as_metadata(r.json())
                if md["authorization_endpoint"]:
                    break
        except Exception:
            continue
    if not md.get("authorization_endpoint"):
        return {"ok": False, "error": f"could not discover OAuth metadata for {server} ({url})"}

    # 2. dynamic client registration (if supported)
    client_id = None
    redirect_uri = "http://localhost:8765/callback"
    if md.get("registration_endpoint"):
        try:
            r = httpx.post(md["registration_endpoint"], json={
                "client_name": "korgex", "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "none"}, timeout=20)
            if r.status_code < 400:
                client_id = r.json().get("client_id")
        except Exception:
            pass
    if not client_id:
        return {"ok": False, "error": "server requires manual client registration; "
                "use `korgex mcp add <name> --url <url> --header \"Authorization: Bearer <token>\"` instead"}

    # 3. browser authorize with PKCE; 4. catch the code on a local callback
    verifier, challenge, method = pkce_pair()
    state = secrets.token_urlsafe(16)
    got = {}

    class _CB(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"korgex: authorized - you can close this tab.")

        def log_message(self, *a):
            return

    srv = http.server.HTTPServer(("localhost", 8765), _CB)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    auth_url = md["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": challenge, "code_challenge_method": method, "state": state})
    print(f"  opening browser to authorize {server}… (or paste this URL)\n  {auth_url}")
    webbrowser.open(auth_url)
    for _ in range(120):
        if got.get("code"):
            break
        time.sleep(1)
    if not got.get("code"):
        return {"ok": False, "error": "timed out waiting for authorization"}

    # 5. exchange the code for tokens
    r = httpx.post(md["token_endpoint"], data={
        "grant_type": "authorization_code", "code": got["code"],
        "redirect_uri": redirect_uri, "client_id": client_id,
        "code_verifier": verifier}, timeout=30)
    if r.status_code >= 400:
        return {"ok": False, "error": f"token exchange failed: {r.status_code} {r.text[:200]}"}
    d = r.json()
    store.set(server, Token(access_token=d["access_token"],
                            refresh_token=d.get("refresh_token"),
                            expires_at=time.time() + int(d.get("expires_in", 3600)),
                            token_url=md["token_endpoint"], client_id=client_id))
    return {"ok": True, "server": server}
