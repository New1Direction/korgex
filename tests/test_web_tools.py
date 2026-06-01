"""Web reach for korgex: WebFetch (read a URL) + WebSearch (DuckDuckGo, no key).

HTML→text extraction, title extraction, and search-result parsing are pure. The
tool entry points take an injected `_get` (url -> (status, text)) so the whole
suite runs offline — no real network.
"""
from src.web_tools import (
    extract_title,
    html_to_text,
    parse_search_results,
    tool_web_fetch,
    tool_web_search,
)


# ── pure HTML helpers ─────────────────────────────────────────────────────────

def test_extract_title():
    assert extract_title("<html><head><title>My Page</title></head>") == "My Page"


def test_extract_title_missing_is_empty():
    assert extract_title("<html><body>no title</body></html>") == ""


def test_html_to_text_strips_tags_scripts_and_styles():
    html = ("<html><body><script>var x=1;</script><style>.a{color:red}</style>"
            "<h1>Hi</h1><p>Hello <b>world</b></p></body></html>")
    text = html_to_text(html)
    assert "Hi" in text and "Hello world" in text
    assert "var x" not in text and "color:red" not in text and "<" not in text


def test_html_to_text_unescapes_entities():
    assert "A & B" in html_to_text("<p>A &amp; B</p>")


# ── WebFetch ──────────────────────────────────────────────────────────────────

def test_web_fetch_rejects_non_http_scheme():
    out = tool_web_fetch("file:///etc/passwd")
    assert "error" in out


def test_web_fetch_returns_title_and_text_via_injected_get():
    def fake_get(url, timeout=20):
        return 200, "<html><head><title>Doc</title></head><body><p>Body text here</p></body></html>"
    out = tool_web_fetch("https://example.com", _get=fake_get)
    assert out["status"] == 200
    assert out["title"] == "Doc"
    assert "Body text here" in out["text"]


def test_web_fetch_truncates_to_max_chars():
    big = "x" * 50000

    def fake_get(url, timeout=20):
        return 200, f"<html><body>{big}</body></html>"
    out = tool_web_fetch("https://e.com", max_chars=1000, _get=fake_get)
    assert len(out["text"]) <= 1200  # truncated (+ a short marker)
    assert out.get("truncated") is True


def test_web_fetch_reports_http_error_status():
    def fake_get(url, timeout=20):
        return 404, "<html>Not found</html>"
    out = tool_web_fetch("https://e.com/x", _get=fake_get)
    assert out["status"] == 404


# ── WebSearch ─────────────────────────────────────────────────────────────────

def test_parse_search_results_extracts_title_url_snippet():
    html = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">'
        'Example Title</a><a class="result__snippet">This is the snippet text.</a>'
        '<a class="result__a" href="https://other.com">Other</a>'
        '<a class="result__snippet">Second snippet.</a>'
    )
    results = parse_search_results(html)
    assert results[0]["title"] == "Example Title"
    assert results[0]["url"] == "https://example.com/page"   # uddg redirect decoded
    assert "snippet text" in results[0]["snippet"]
    assert results[1]["url"] == "https://other.com"


def test_web_search_returns_results_via_injected_get():
    ddg = '<a class="result__a" href="https://r1.com">R1</a><a class="result__snippet">snip1</a>'

    def fake_get(url, timeout=20):
        assert "duckduckgo" in url and "q=" in url   # it hit the search endpoint
        return 200, ddg
    out = tool_web_search("python asyncio", _get=fake_get)
    assert out["query"] == "python asyncio"
    assert out["results"][0]["url"] == "https://r1.com"


def test_web_search_limits_results():
    many = "".join(
        f'<a class="result__a" href="https://r{i}.com">R{i}</a>'
        f'<a class="result__snippet">s{i}</a>' for i in range(10))

    def fake_get(url, timeout=20):
        return 200, many
    out = tool_web_search("q", max_results=3, _get=fake_get)
    assert len(out["results"]) == 3


# ── registration + routing into the agent's toolset ───────────────────────────

def test_web_tools_are_registered_and_directly_visible():
    from src.tool_abstraction import USER_TOOLS, visible_tool_names
    assert "WebFetch" in USER_TOOLS and "WebSearch" in USER_TOOLS
    vis = visible_tool_names()
    assert "WebFetch" in vis and "WebSearch" in vis   # direct exposure, always offered


def test_route_tool_call_dispatches_webfetch():
    # file:// is rejected by the handler itself, so this exercises routing with no network.
    from src.tool_abstraction import route_tool_call
    out = route_tool_call("WebFetch", {"url": "file:///etc/passwd"})
    assert "error" in out


def test_route_tool_call_dispatches_websearch(monkeypatch):
    import src.web_tools as W
    from src.tool_abstraction import route_tool_call
    monkeypatch.setattr(W, "_http_get", lambda url, timeout=20: (
        200, '<a class="result__a" href="https://r.com">R</a><a class="result__snippet">s</a>'))
    out = route_tool_call("WebSearch", {"query": "hello"})
    assert out["results"][0]["url"] == "https://r.com"
