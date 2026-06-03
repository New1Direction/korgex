"""WebSearch backends — a self-hosted SearXNG (JSON) primary with a DuckDuckGo
fallback, plus an opt-in Camoufox stealth fetch path for bot-walled pages.

The HTTP layer is injected, so backend selection + parsing test fully offline. The
Camoufox *drive* runs only when the `camoufox` package is installed and
KORGEX_WEB_STEALTH is on — here it's monkeypatched, so we pin the gating/selection,
not the browser.
"""
from __future__ import annotations

import json

from src import web_tools as W

SEARX_JSON = {
    "query": "rust async",
    "number_of_results": 2,
    "results": [
        {"url": "https://ex.com/a", "title": "Async Rust", "content": "a guide to async", "engine": "google"},
        {"url": "https://ex.com/b", "title": "Tokio", "content": "the runtime", "engine": "bing"},
    ],
}


def test_parse_searxng_json():
    assert W.parse_searxng_json(SEARX_JSON) == [
        {"title": "Async Rust", "url": "https://ex.com/a", "snippet": "a guide to async"},
        {"title": "Tokio", "url": "https://ex.com/b", "snippet": "the runtime"},
    ]


def test_parse_searxng_json_tolerates_garbage():
    assert W.parse_searxng_json({}) == []
    assert W.parse_searxng_json({"results": "nope"}) == []
    assert W.parse_searxng_json(None) == []


def test_web_search_uses_searxng_when_configured(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8888")
    seen = {}

    def fake_get(url, timeout=20):
        seen["url"] = url
        return 200, json.dumps(SEARX_JSON)

    res = W.tool_web_search("rust async", _get=fake_get)
    assert res["engine"] == "searxng"
    assert "format=json" in seen["url"] and "localhost:8888" in seen["url"]
    assert res["results"][0]["title"] == "Async Rust"
    assert res["count"] == 2


def test_web_search_falls_back_to_ddg_when_no_searxng(monkeypatch):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    ddg = '<a class="result__a" href="https://x.com">Hit</a><a class="result__snippet">snip</a>'
    res = W.tool_web_search("q", _get=lambda url, timeout=20: (200, ddg))
    assert res["engine"] == "duckduckgo"
    assert res["results"][0]["url"] == "https://x.com"


def test_web_search_falls_back_to_ddg_when_searxng_errors(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://localhost:8888")
    ddg = '<a class="result__a" href="https://x.com">Hit</a>'

    def fake_get(url, timeout=20):
        return (500, "upstream boom") if "format=json" in url else (200, ddg)

    res = W.tool_web_search("q", _get=fake_get)
    assert res["engine"] == "duckduckgo"
    assert res["results"][0]["url"] == "https://x.com"


def test_stealth_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("KORGEX_WEB_STEALTH", raising=False)
    assert W._web_stealth_enabled() is False
    for v in ("1", "true", "yes", "on", "ON"):
        monkeypatch.setenv("KORGEX_WEB_STEALTH", v)
        assert W._web_stealth_enabled() is True


def test_default_get_uses_plain_http_when_stealth_off(monkeypatch):
    monkeypatch.delenv("KORGEX_WEB_STEALTH", raising=False)
    seen = {}
    monkeypatch.setattr(W, "_http_get", lambda url, timeout=20: (seen.setdefault("plain", True), (200, "PLAIN"))[1])
    monkeypatch.setattr(W, "camoufox_get", lambda *a, **k: (seen.setdefault("stealth", True), (200, "S"))[1])
    assert W._default_get("https://x.com") == (200, "PLAIN")
    assert seen.get("plain") and "stealth" not in seen


def test_default_get_uses_camoufox_when_enabled_and_available(monkeypatch):
    monkeypatch.setenv("KORGEX_WEB_STEALTH", "1")
    monkeypatch.setattr(W, "_camoufox_available", lambda: True)
    monkeypatch.setattr(W, "camoufox_get", lambda url, timeout=30: (200, "STEALTH-HTML"))
    monkeypatch.setattr(W, "_http_get", lambda url, timeout=20: (200, "PLAIN"))
    assert W._default_get("https://x.com") == (200, "STEALTH-HTML")


def test_default_get_falls_back_to_plain_when_camoufox_unavailable(monkeypatch):
    monkeypatch.setenv("KORGEX_WEB_STEALTH", "1")
    monkeypatch.setattr(W, "_camoufox_available", lambda: False)
    monkeypatch.setattr(W, "_http_get", lambda url, timeout=20: (200, "PLAIN"))
    assert W._default_get("https://x.com") == (200, "PLAIN")
