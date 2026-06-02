"""Offline tests for SLICE 3 deterministic page audit (browser_audit).

build_audit() turns one snapshot + response headers + a link inventory into a
DETERMINISTIC structured report (title/meta/canonical, heading outline, link
inventory + broken links, JSON-LD validity, hreflang, security headers). Because
the output keys are sorted and the input is pure, two runs on identical input
hash-equal — a SEALABLE verifiable artifact (pairs with custody/artifact_index).

All pure: no browser, no network. The tool handler accepts an injected fake
`_session` so even the route-level test runs offline.
"""
from __future__ import annotations

import hashlib
import json

from src import browser as B
from src import tools_impl as T


PAGE_HTML = """
<html>
  <head>
    <title>  Example Page </title>
    <meta name="description" content="A demo page">
    <link rel="canonical" href="https://e.com/canonical">
    <link rel="alternate" hreflang="en" href="https://e.com/en">
    <link rel="alternate" hreflang="fr" href="https://e.com/fr">
    <script type="application/ld+json">{"@type": "WebPage", "name": "ok"}</script>
  </head>
  <body>
    <h1>Top</h1>
    <h2>Sub A</h2>
    <h3>Deep</h3>
    <h2>Sub B</h2>
    <a href="/a">A</a>
    <a href="/b">B</a>
  </body>
</html>
"""


def test_build_audit_extracts_core_signals():
    rep = B.build_audit(
        PAGE_HTML,
        headers={"strict-transport-security": "max-age=1",
                 "content-security-policy": "default-src 'self'"},
        links=[{"href": "/a", "status": 200}, {"href": "/b", "status": 404}],
    )
    assert rep["title"] == "Example Page"
    assert rep["meta"]["description"] == "A demo page"
    assert rep["meta"]["canonical"] == "https://e.com/canonical"
    # heading outline preserves document order with levels
    assert rep["heading_outline"] == [
        [1, "Top"], [2, "Sub A"], [3, "Deep"], [2, "Sub B"],
    ]
    # link inventory + broken subset
    assert rep["link_inventory"]["broken"] == [{"href": "/b", "status": 404}]
    assert rep["link_inventory"]["total"] == 2
    # JSON-LD present + valid
    assert rep["jsonld"]["present"] is True
    assert rep["jsonld"]["valid"] is True
    # hreflang set (sorted)
    assert rep["hreflang"] == ["en", "fr"]
    # security headers
    assert rep["security"]["hsts"] is True
    assert rep["security"]["csp"] is True
    assert rep["security"]["x_frame_options"] is False


def test_build_audit_is_deterministic_and_sealable():
    args = dict(
        page=PAGE_HTML,
        headers={"strict-transport-security": "max-age=1"},
        links=[{"href": "/a", "status": 200}],
    )
    a = B.build_audit(**args)
    b = B.build_audit(**args)
    # byte-identical canonical JSON => same seal hash
    ja = json.dumps(a, sort_keys=True)
    jb = json.dumps(b, sort_keys=True)
    assert ja == jb
    ha = hashlib.sha256(ja.encode()).hexdigest()
    hb = hashlib.sha256(jb.encode()).hexdigest()
    assert ha == hb and len(ha) == 64


def test_build_audit_handles_malformed_jsonld_without_raising():
    html = ('<title>x</title>'
            '<script type="application/ld+json">{not valid json,,}</script>')
    rep = B.build_audit(html, headers={}, links=[])
    assert rep["jsonld"]["present"] is True
    assert rep["jsonld"]["valid"] is False
    # absent header => security flags all False but present as keys (stable shape)
    assert rep["security"]["hsts"] is False
    assert "x_content_type_options" in rep["security"]


def test_build_audit_accepts_snapshot_dict_with_html():
    # build_audit accepts either raw HTML or a snapshot-shaped dict carrying html.
    rep = B.build_audit({"html": PAGE_HTML, "url": "https://e.com/"},
                        headers={}, links=[])
    assert rep["title"] == "Example Page"


# ── tool wiring ──────────────────────────────────────────────────────────────

class _FakeCDP:
    """Local fake CDPTransport (the codebase has no shared test package, so each
    browser test file defines its own — see test_browser_actions/_fetch)."""

    def __init__(self, responses=None):
        self._r = responses or {}
        self.calls = []

    def send(self, method, params=None):
        self.calls.append((method, params))
        return self._r.get(method, {})

    def detach(self):
        pass


class _FakeAuditPage:
    def __init__(self, html, url="https://e.com/"):
        self._html = html
        self._url = url

    def url(self):
        return self._url

    def goto(self, url, **kw):
        self._url = url

    def content(self):
        return self._html


def _fake_audit_session():
    return B.BrowserSession(
        client=_FakeCDP({
            "DOM.getDocument": {"root": {"backendNodeId": 1, "nodeName": "BODY",
                                         "attributes": [], "children": []}},
            "Accessibility.getFullAXTree": {"nodes": []},
        }),
        page=_FakeAuditPage(PAGE_HTML),
        driver="playwright",
    )


def test_tool_browser_audit_returns_deterministic_report():
    sess = _fake_audit_session()
    out = T.tool_browser_audit(url="https://e.com/", _session=sess)
    assert out["ok"] is True
    rep = out["report"]
    assert rep["title"] == "Example Page"
    assert rep["heading_outline"][0] == [1, "Top"]
    # the handler also seals the report (stable hash of canonical JSON)
    expect = hashlib.sha256(json.dumps(rep, sort_keys=True).encode()).hexdigest()
    assert out["report_hash"] == expect


def test_tool_browser_audit_degrades_without_browser(monkeypatch):
    def boom(*a, **k):
        raise B.BrowserUnavailable(B._INSTALL_HINT)

    monkeypatch.setattr(B, "default_session", boom)
    out = T.tool_browser_audit(url="https://e.com/")
    assert "error" in out
    assert "playwright install chromium" in out["error"]


def test_browser_audit_is_registered_and_routed():
    from src.tool_abstraction import _TOOL_ROUTING, USER_TOOLS
    assert "browser_audit" in USER_TOOLS
    assert USER_TOOLS["browser_audit"]["exposure"] == "deferred"
    assert "browser_audit" in _TOOL_ROUTING
    assert _TOOL_ROUTING["browser_audit"]["module"] == "src.tools_impl"
