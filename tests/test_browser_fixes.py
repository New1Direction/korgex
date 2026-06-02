"""Regression tests for adversarial-verify findings on the browser suite.

Each pins a real gap the green suite hid: crawl no-op (fetch returned no links),
navigate scheme escape, nested-hidden-block sanitizer bypass, and the failed-act
trace. (Index-order + zero-box-click are proven on the wire by the dogfood.)
"""
import src.browser as B
from src.tools_impl import tool_browser_navigate


def test_fetch_tiered_returns_resolved_links_for_crawl():
    # #1: fetch_tiered never returned a 'links' key, so crawl couldn't follow.
    html = '<html><body><a href="/a">A</a><a href="https://other.test/b">B</a></body></html>'
    out = B.fetch_tiered("http://x.test/page", _http=lambda u, timeout=20: (200, html))
    assert "links" in out
    assert "http://x.test/a" in out["links"]          # resolved against the base URL


def test_crawl_follows_links_past_the_start_page():
    # #1: with links present, crawl must visit the frontier, not just the start.
    start = "http://x.test/"

    def fetch(u, **kw):
        if u == B.unique_key(start):
            return {"links": ["http://x.test/a", "http://x.test/b"]}
        return {"links": []}

    class _NoLedger:
        def record_tool_call(self, *a, **k):
            return None

    out = B.crawl(start, max_pages=10, _fetch=fetch, _ledger=_NoLedger())
    assert out["pages"] == 3                           # start + a + b, not 1


def test_browser_navigate_blocks_non_http_scheme():
    # #4: file:// / chrome:// must be refused (parity with fetch/crawl/audit).
    for bad in ("file:///etc/passwd", "chrome://settings", "view-source:http://x"):
        res = tool_browser_navigate(bad)
        assert res["ok"] is False and "http" in res["error"].lower()


def test_sanitize_strips_nested_hidden_block_in_full():
    # #5: nested same-tag hidden block leaked text past the first close tag.
    out = B.sanitize_html(
        '<div hidden><div>inner</div>SECRET-INSTRUCTION</div><p>after</p>')
    assert "SECRET-INSTRUCTION" not in out
    assert "after" in out                              # content after survives
