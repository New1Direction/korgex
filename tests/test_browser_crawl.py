"""Offline tests for SLICE 3 crawl primitives + browser_crawl.

The crawl layer is a THIN, ledger-emitting reimplementation of the well-known
crawl primitives (normalized dedup, same-host/same-domain enqueue scoping, an
even-spread rate limit, and session error-scoring) — it does NOT depend on any
crawl framework. Every visited page becomes a hash-chained ledger fact, so a
crawl is auditable end to end (`korgex trace` / `korgex verify`).

Everything runs offline: the fetch transport is injected via `_fetch`, the
ledger via `_ledger` (a FakeLedger), and the rate limiter's clock/sleep are
injected so no test ever sleeps for real. No browser, no network.
"""
from __future__ import annotations

from src import browser as B
from src import tools_impl as T


class FakeLedger:
    """Records record_tool_call(...) calls so a test can assert one ledger
    event per visited page. Mirrors the KorgLedgerClient surface used by the
    agent loop (tool_name, args, result, success, duration_ms)."""

    def __init__(self):
        self.calls = []

    def record_tool_call(self, tool_name, args, result, success, duration_ms,
                         triggered_by=None):
        self.calls.append({
            "tool_name": tool_name, "args": args, "result": result,
            "success": success, "duration_ms": duration_ms,
            "triggered_by": triggered_by,
        })
        return len(self.calls)


# ── Step 1: pure crawl primitives ────────────────────────────────────────────

def test_normalize_url_lowercases_host_sorts_query_strips_fragment():
    assert B.normalize_url("HTTP://Ex.com/p?b=2&a=1#frag") == "http://ex.com/p?a=1&b=2"


def test_unique_key_collapses_fragment_and_param_order():
    a = B.unique_key("https://x.com/p?a=1&b=2")
    b = B.unique_key("https://x.com/p?b=2&a=1#section")
    assert a == b


def test_same_host_and_same_domain():
    assert B.same_host("https://x.com/a", "https://x.com/b") is True
    assert B.same_host("https://x.com", "https://y.com") is False
    # same_domain ignores the subdomain; same_host does not
    assert B.same_domain("https://www.x.com/a", "https://blog.x.com/b") is True
    assert B.same_host("https://www.x.com/a", "https://blog.x.com/b") is False
    assert B.same_domain("https://x.com", "https://y.com") is False


def test_rate_limiter_enforces_even_spread_without_real_sleep():
    # An injected clock + sleeper: no wall-clock time passes. The 2nd acquire
    # within the window must request a sleep of ~min_interval (even spread).
    slept = []
    now = [100.0]
    rl = B.RateLimiter(min_interval=0.5,
                       _clock=lambda: now[0],
                       _sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    rl.acquire()           # first call: no wait
    assert slept == []
    rl.acquire()           # immediately after: must wait ~0.5
    assert len(slept) == 1
    assert abs(slept[0] - 0.5) < 1e-6


def test_rate_limiter_no_wait_when_interval_elapsed():
    slept = []
    now = [0.0]
    rl = B.RateLimiter(min_interval=0.5,
                       _clock=lambda: now[0],
                       _sleep=lambda s: slept.append(s))
    rl.acquire()
    now[0] = 10.0          # plenty of time passed
    rl.acquire()
    assert slept == []     # no throttling needed


def test_session_score_rotates_after_threshold_and_resets_on_ok():
    s = B.SessionScore(max_errors=3)
    assert s.should_rotate() is False
    s.record_error()
    s.record_error()
    assert s.should_rotate() is False   # 2 < 3
    s.record_error()
    assert s.should_rotate() is True    # 3 >= 3
    s.record_ok()
    assert s.should_rotate() is False   # streak reset


# ── Step 2: browser_crawl walks with dedup + same-host rail + ledger record ───

def _link_graph_fetch():
    """A fake fetch returning a tiny link graph keyed by normalized URL. Includes
    an off-host link (must NOT be enqueued) and a fragment-duplicate of /a (must
    be deduped to one visit)."""
    pages = {
        "https://site.com/": {
            "markdown": "home",
            "links": ["https://site.com/a",
                      "https://site.com/a#again",       # dup of /a
                      "https://other.com/x"],            # off-host
        },
        "https://site.com/a": {
            "markdown": "page a",
            "links": ["https://site.com/b", "https://site.com/"],  # / already seen
        },
        "https://site.com/b": {"markdown": "page b", "links": []},
    }

    def fake_fetch(url, **kw):
        key = B.normalize_url(url)
        page = pages.get(key, {"markdown": "", "links": []})
        return {"url": key, "markdown": page["markdown"],
                "links": page["links"], "status": 200}

    return fake_fetch


def test_crawl_dedups_scopes_to_host_and_records_each_page():
    led = FakeLedger()
    out = B.crawl("https://site.com/", max_pages=10, same_host=True,
                  _fetch=_link_graph_fetch(), _ledger=led)

    visited = out["visited"]
    # off-host never enqueued; fragment-dup collapsed; / visited once
    assert "https://other.com/x" not in visited
    assert visited.count("https://site.com/a") == 1
    assert visited.count("https://site.com/") == 1
    assert set(visited) == {"https://site.com/", "https://site.com/a", "https://site.com/b"}
    # one ledger event per visited page, all named browser.crawl_page
    assert len(led.calls) == len(visited)
    assert all(c["tool_name"] == "browser.crawl_page" for c in led.calls)
    # the trace carries url + depth + links_found
    first = led.calls[0]
    assert first["args"]["url"] == "https://site.com/"
    assert first["args"]["depth"] == 0
    assert "links_found" in first["result"]


def test_crawl_respects_max_pages():
    led = FakeLedger()
    out = B.crawl("https://site.com/", max_pages=2, same_host=True,
                  _fetch=_link_graph_fetch(), _ledger=led)
    assert out["pages"] == 2
    assert len(out["visited"]) == 2
    assert len(led.calls) == 2


def test_crawl_same_domain_allows_subdomains():
    pages = {
        "https://www.site.com/": {"links": ["https://blog.site.com/post"]},
        "https://blog.site.com/post": {"links": []},
    }

    def fake_fetch(url, **kw):
        key = B.normalize_url(url)
        return {"url": key, "markdown": "x",
                "links": pages.get(key, {}).get("links", []), "status": 200}

    out = B.crawl("https://www.site.com/", max_pages=10, same_host=False,
                  same_domain=True, _fetch=fake_fetch, _ledger=FakeLedger())
    assert "https://blog.site.com/post" in out["visited"]


# ── Step 6: browser_crawl tool wiring ─────────────────────────────────────────

def test_tool_browser_crawl_returns_visited():
    led = FakeLedger()
    out = T.tool_browser_crawl(start_url="https://site.com/", max_pages=3,
                               _fetch=_link_graph_fetch(), _ledger=led)
    assert out["ok"] is True
    assert "https://site.com/" in out["visited"]
    assert len(led.calls) == len(out["visited"])


def test_tool_browser_crawl_rejects_non_http_url():
    out = T.tool_browser_crawl(start_url="file:///etc/passwd")
    assert "error" in out


def test_browser_crawl_is_registered_and_routed():
    from src.tool_abstraction import _TOOL_ROUTING, USER_TOOLS
    assert "browser_crawl" in USER_TOOLS
    assert USER_TOOLS["browser_crawl"]["exposure"] == "deferred"
    assert "browser_crawl" in _TOOL_ROUTING
    assert _TOOL_ROUTING["browser_crawl"]["module"] == "src.tools_impl"
