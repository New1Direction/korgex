"""Offline tests for the geometric act path and the verifiable-trace tool
handlers (Slice 1). No browser, no playwright import — a FakeCDP is injected.

The headline assertion: every browser_* act tool returns the verifiable trace
dict {ok, action, index, backend_node_id, url, pre_snapshot_hash,
post_snapshot_hash, driver}. That dict is what korgex's existing
record_tool_call ledgers, so `korgex trace`/`korgex verify` can prove the
perceive→act DAG with zero new ledger code.
"""
from __future__ import annotations

from src import browser as B
from src import tools_impl as T


class FakeCDP:
    """A fake CDPTransport: records (method, params), returns canned JSON keyed
    by method. No browser, no playwright."""

    def __init__(self, responses=None):
        self._r = responses or {}
        self.calls = []
        self.detached = False

    def send(self, method, params=None):
        self.calls.append((method, params))
        return self._r.get(method, {})

    def detach(self):
        self.detached = True

    def methods(self):
        return [m for (m, _) in self.calls]


class FakePage:
    def __init__(self, url="https://example.com/"):
        self._url = url
        self.goto_calls = []

    def url(self):
        return self._url

    def goto(self, url, **kw):
        self.goto_calls.append(url)
        self._url = url

    def screenshot(self, **kw):
        return b"\x89PNG-fake-bytes"


SLICE1_DOM = {
    "DOM.getDocument": {
        "root": {
            "backendNodeId": 1,
            "nodeName": "BODY",
            "attributes": [],
            "children": [
                {"backendNodeId": 42, "nodeName": "BUTTON", "attributes": [], "children": []},
                {"backendNodeId": 43, "nodeName": "DIV",
                 "attributes": ["aria-hidden", "true"], "children": []},
            ],
        }
    },
    "Accessibility.getFullAXTree": {
        "nodes": [
            {"backendDOMNodeId": 42, "role": {"value": "button"},
             "name": {"value": "Submit"}, "ignored": False},
            {"backendDOMNodeId": 43, "role": {"value": "generic"}, "ignored": True},
        ]
    },
}


# A FakeCDP that also answers DOM.getBoxModel so click() can compute a center.
def _act_cdp(extra=None):
    responses = dict(SLICE1_DOM)
    responses["DOM.getBoxModel"] = {"model": {"content": [10, 10, 30, 10, 30, 20, 10, 20]}}
    if extra:
        responses.update(extra)
    return FakeCDP(responses)


# ── Step 4: geometric click resolves index → backend_node_id → Input events ──

def test_click_resolves_index_and_dispatches_centered_mouse_events():
    fake = _act_cdp()
    sess = B.BrowserSession(client=fake, page=FakePage())
    sess.selector_map = {0: 42}
    sess.click(0)

    assert ("DOM.scrollIntoViewIfNeeded", {"backendNodeId": 42}) in fake.calls
    assert ("DOM.getBoxModel", {"backendNodeId": 42}) in fake.calls
    # three mouse events, centered at (20, 15) — mean of the content quad corners
    mouse = [(m, p) for (m, p) in fake.calls if m == "Input.dispatchMouseEvent"]
    assert [p["type"] for (_, p) in mouse] == ["mouseMoved", "mousePressed", "mouseReleased"]
    for _, p in mouse:
        assert p["x"] == 20 and p["y"] == 15
    pressed = [p for (_, p) in mouse if p["type"] == "mousePressed"][0]
    assert pressed["button"] == "left" and pressed["clickCount"] == 1


def test_click_unknown_index_raises_clearly():
    sess = B.BrowserSession(client=_act_cdp(), page=FakePage())
    sess.selector_map = {0: 42}
    try:
        sess.click(99)
        raised = False
    except B.BrowserUnavailable:
        raised = True
    assert raised


# ── Step 5: type() focuses then inserts text ─────────────────────────────────

def test_type_focuses_then_inserts_text():
    fake = _act_cdp()
    sess = B.BrowserSession(client=fake, page=FakePage())
    sess.selector_map = {0: 42}
    sess.type(0, "hello")
    assert ("DOM.focus", {"backendNodeId": 42}) in fake.calls
    assert ("Input.insertText", {"text": "hello"}) in fake.calls
    # focus immediately precedes insertText
    i_focus = fake.calls.index(("DOM.focus", {"backendNodeId": 42}))
    i_insert = fake.calls.index(("Input.insertText", {"text": "hello"}))
    assert i_insert == i_focus + 1


# ── Step 6: tool handlers emit the verifiable trace dict ─────────────────────

def _trace_keys(d):
    return {"ok", "action", "index", "backend_node_id", "url",
            "pre_snapshot_hash", "post_snapshot_hash", "driver"} <= set(d)


def test_tool_browser_click_returns_verifiable_trace():
    sess = B.BrowserSession(client=_act_cdp(), page=FakePage(), driver="playwright")
    sess.snapshot()  # populate selector_map {0: 42}
    out = T.tool_browser_click(index=0, _session=sess)
    assert _trace_keys(out)
    assert out["ok"] is True
    assert out["action"] == "click"
    assert out["index"] == 0
    assert out["backend_node_id"] == 42
    assert out["driver"] == "playwright"
    assert len(out["pre_snapshot_hash"]) == 64
    assert len(out["post_snapshot_hash"]) == 64


def test_tool_browser_navigate_returns_trace_with_post_hash():
    sess = B.BrowserSession(client=_act_cdp(), page=FakePage("https://old/"), driver="playwright")
    out = T.tool_browser_navigate(url="https://new/page", _session=sess)
    assert out["ok"] is True
    assert out["action"] == "navigate"
    assert out["url"] == "https://new/page"
    assert len(out["post_snapshot_hash"]) == 64


def test_tool_browser_type_returns_trace():
    sess = B.BrowserSession(client=_act_cdp(), page=FakePage())
    sess.snapshot()
    out = T.tool_browser_type(index=0, text="hi", _session=sess)
    assert out["ok"] is True
    assert out["action"] == "type"
    assert out["backend_node_id"] == 42


def test_tool_browser_snapshot_returns_hash_and_text():
    sess = B.BrowserSession(client=_act_cdp(), page=FakePage())
    out = T.tool_browser_snapshot(_session=sess)
    assert out["ok"] is True
    assert len(out["snapshot_hash"]) == 64
    assert "[0] <button> Submit" in out["text"]
    assert any(i["backend_node_id"] == 42 for i in out["interactives"])
    assert out["url"] == "https://example.com/"


def test_browser_tools_degrade_without_browser(monkeypatch):
    # default_session() raises BrowserUnavailable when no browser is present;
    # the handler must catch it and return a clear install hint, not crash.
    def boom(*a, **k):
        raise B.BrowserUnavailable(B._INSTALL_HINT)

    monkeypatch.setattr(B, "default_session", boom)
    out = T.tool_browser_snapshot()  # no _session -> falls back to default_session
    assert "error" in out
    assert "playwright install chromium" in out["error"]


def test_tool_browser_scroll_and_evaluate_and_wait():
    sess = B.BrowserSession(client=_act_cdp(
        {"Runtime.evaluate": {"result": {"value": 7}}}), page=FakePage())
    s = T.tool_browser_scroll(dy=200, _session=sess)
    assert s["ok"] is True and s["action"] == "scroll"
    e = T.tool_browser_evaluate(expression="1+6", _session=sess)
    assert e["ok"] is True and e["value"] == 7
    w = T.tool_browser_wait(ms=5, _session=sess)
    assert w["ok"] is True


# ── Step 8: registration + routing ───────────────────────────────────────────

BROWSER_TOOLS_S1 = {
    "browser_navigate", "browser_snapshot", "browser_click", "browser_type",
    "browser_extract", "browser_screenshot", "browser_evaluate",
    "browser_wait", "browser_scroll",
}


def test_browser_tools_are_registered_and_routed():
    from src.tool_abstraction import _TOOL_ROUTING, USER_TOOLS
    assert BROWSER_TOOLS_S1 <= set(USER_TOOLS)
    for name in BROWSER_TOOLS_S1:
        assert name in _TOOL_ROUTING
        assert _TOOL_ROUTING[name]["module"] == "src.tools_impl"
        # deferred exposure: large extra surface discovered via ToolSearch
        assert USER_TOOLS[name]["exposure"] == "deferred"


def test_route_tool_call_browser_snapshot(monkeypatch):
    sess = B.BrowserSession(client=_act_cdp(), page=FakePage())
    monkeypatch.setattr(B, "default_session", lambda *a, **k: sess)
    from src.tool_abstraction import route_tool_call
    out = route_tool_call("browser_snapshot", {})
    assert "snapshot_hash" in out
