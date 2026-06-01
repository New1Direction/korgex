"""OAuth for remote MCP servers — the testable core: a token store with refresh,
auto-applied as a Bearer header, plus PKCE + metadata-discovery helpers. The live
browser/callback leg of `login()` is verified in a real session; everything here
is pure/injected so it tests offline.
"""
from src.mcp_oauth import (
    Token,
    TokenStore,
    auth_header,
    is_expired,
    parse_as_metadata,
    pkce_pair,
    valid_token,
)


def test_token_store_set_get_persist(tmp_path):
    p = str(tmp_path / "tok.json")
    TokenStore(p).set("linear", Token(access_token="abc", refresh_token="r", expires_at=999))
    t = TokenStore(p).get("linear")              # fresh instance reads from disk
    assert t.access_token == "abc" and t.refresh_token == "r"


def test_is_expired_with_skew():
    now = 1000.0
    assert is_expired(Token("a", expires_at=1000 - 1), now) is True      # already past
    assert is_expired(Token("a", expires_at=now + 30), now, skew=120) is True   # within skew
    assert is_expired(Token("a", expires_at=now + 9999), now) is False
    assert is_expired(Token("a", expires_at=0), now) is False            # 0 = no expiry


def test_valid_token_refreshes_when_expired(tmp_path):
    store = TokenStore(str(tmp_path / "tok.json"))
    store.set("sentry", Token(access_token="old", refresh_token="R", expires_at=1.0))
    calls = {}

    def refresher(refresh_token):
        calls["rt"] = refresh_token
        return Token(access_token="new", refresh_token="R2", expires_at=10_000_000_000)

    tok = valid_token(store, "sentry", now=2.0, refresher=refresher)
    assert tok == "new" and calls["rt"] == "R"
    assert store.get("sentry").access_token == "new"   # persisted


def test_valid_token_returns_current_when_fresh(tmp_path):
    store = TokenStore(str(tmp_path / "tok.json"))
    store.set("x", Token(access_token="live", expires_at=10_000_000_000))
    assert valid_token(store, "x", now=2.0, refresher=lambda r: None) == "live"


def test_auth_header_builds_bearer(tmp_path):
    store = TokenStore(str(tmp_path / "tok.json"))
    store.set("x", Token(access_token="tok123", expires_at=10_000_000_000))
    assert auth_header(store, "x", now=1.0) == {"Authorization": "Bearer tok123"}
    assert auth_header(store, "missing", now=1.0) == {}   # no token → no header


def test_pkce_pair_is_valid():
    verifier, challenge, method = pkce_pair()
    assert 43 <= len(verifier) <= 128 and method == "S256" and challenge


def test_parse_as_metadata_extracts_endpoints():
    md = parse_as_metadata({
        "authorization_endpoint": "https://a/authorize",
        "token_endpoint": "https://a/token",
        "registration_endpoint": "https://a/register",
    })
    assert md["authorization_endpoint"].endswith("/authorize")
    assert md["token_endpoint"].endswith("/token")
