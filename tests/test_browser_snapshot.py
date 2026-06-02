"""Offline tests for the verifiable CDP snapshot→index core (src/browser.py).

The whole browser layer is exercised with NO real browser and NO playwright
import: a FakeCDP transport (a thing with .send/.detach, the CDPTransport
Protocol) is injected into BrowserSession, mirroring web_tools' `_get` seam.

The first test pins the load-bearing invariant: browser.py imports STDLIB ONLY
at module scope. playwright IS installed in this dev env but is NOT a declared
[project].dependency, so a top-level `import playwright` would break
tests/test_no_undeclared_module_imports.py. This AST check fails loudly if that
regresses.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import inspect

from src import browser as B


class FakeCDP:
    """A fake CDPTransport: records (method, params) calls, returns canned JSON
    keyed by method. No browser, no playwright."""

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
    """Minimal page facade: just enough for url()/goto()/screenshot()."""

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


# canned protocol JSON for a tiny page: a real <button>Submit</button> (bid 42)
# and an aria-hidden <div> (bid 43) that must be excluded from interactives.
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


# ── Step 1: harness + module skeleton, no playwright at module scope ──────────

def test_browser_session_holds_injected_client():
    sess = B.BrowserSession(client=FakeCDP({}))
    assert sess.client is not None
    assert sess.selector_map == {}
    assert sess.driver == "playwright"


def test_browser_module_imports_are_stdlib_only():
    """No module-level playwright/patchright/curl_cffi import. This is the
    contract that keeps test_no_undeclared_module_imports green."""
    allowed = {"hashlib", "json", "importlib", "re", "time", "typing",
               "urllib", "__future__", "os"}
    tree = ast.parse(inspect.getsource(B))
    roots = set()
    for node in tree.body:  # direct children only -> module scope
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    undeclared = roots - allowed
    assert not undeclared, f"non-stdlib module-level imports in browser.py: {undeclared}"


# ── Step 2: AX↔DOM join + interactive classification + indexing ──────────────

def test_snapshot_joins_ax_and_dom_and_indexes_interactives():
    sess = B.BrowserSession(client=FakeCDP(SLICE1_DOM), page=FakePage())
    snap = sess.snapshot()
    by_bid = {n["backend_node_id"]: n for n in snap["interactives"]}
    assert 42 in by_bid
    assert by_bid[42]["role"] == "button"
    assert by_bid[42]["name"] == "Submit"
    # aria-hidden / ax-ignored node 43 is excluded
    assert 43 not in by_bid
    # sequential index -> backend_node_id
    assert sess.selector_map == {0: 42}


def test_snapshot_issues_expected_cdp_calls():
    fake = FakeCDP(SLICE1_DOM)
    sess = B.BrowserSession(client=fake, page=FakePage())
    sess.snapshot()
    assert ("Accessibility.enable", None) in fake.calls or \
        any(m == "Accessibility.enable" for (m, _) in fake.calls)
    assert ("DOM.getDocument", {"depth": -1, "pierce": True}) in fake.calls
    assert ("Accessibility.getFullAXTree", {"depth": -1}) in fake.calls


def test_classify_interactive_native_tag_role_and_listener_heuristic():
    # native tag
    assert B.classify_interactive({"nodeName": "A"}, ax_role="link", listeners=[]) is True
    # SPA listener heuristic: a DIV with a click handler is interactive
    assert B.classify_interactive({"nodeName": "DIV"}, ax_role="generic",
                                  listeners=[{"type": "click"}]) is True
    # plain DIV, no role / listener / native tag -> not interactive
    assert B.classify_interactive({"nodeName": "DIV"}, ax_role="generic", listeners=[]) is False
    # actionable ARIA role on a non-native tag
    assert B.classify_interactive({"nodeName": "SPAN"}, ax_role="button", listeners=[]) is True


# ── Step 3: compact serialization + snapshot hash ────────────────────────────

def test_serialize_snapshot_emits_indexed_lines():
    sess = B.BrowserSession(client=FakeCDP(SLICE1_DOM), page=FakePage())
    snap = sess.snapshot()
    text = B.serialize_snapshot(snap)
    assert "[0] <button> Submit" in text


def test_snapshot_hash_is_stable_sha256_of_serialization():
    sess = B.BrowserSession(client=FakeCDP(SLICE1_DOM), page=FakePage())
    snap = sess.snapshot()
    h = B.snapshot_hash(snap)
    assert h == hashlib.sha256(B.serialize_snapshot(snap).encode()).hexdigest()
    assert len(h) == 64
    int(h, 16)  # valid hex


def test_snapshot_hash_determinism_and_sensitivity():
    snap_a = {"url": "u", "interactives": [
        {"index": 0, "backend_node_id": 42, "tag": "button", "role": "button", "name": "Submit"}]}
    snap_b = {"url": "u", "interactives": [
        {"index": 0, "backend_node_id": 42, "tag": "button", "role": "button", "name": "Submit"}]}
    snap_c = {"url": "u", "interactives": [
        {"index": 0, "backend_node_id": 42, "tag": "button", "role": "button", "name": "Cancel"}]}
    assert B.snapshot_hash(snap_a) == B.snapshot_hash(snap_b)
    assert B.snapshot_hash(snap_a) != B.snapshot_hash(snap_c)


# ── Step 7: open_session is the ONLY playwright touch-point; degrades clearly ─

def test_open_session_degrades_without_playwright(monkeypatch):
    real_import = importlib.import_module

    def fake_import(name, *a, **k):
        if name.startswith("playwright") or name.startswith("patchright"):
            raise ImportError("no playwright here")
        return real_import(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    try:
        B.open_session()
        raised = False
    except B.BrowserUnavailable as e:
        raised = True
        assert "playwright install chromium" in str(e)
    assert raised, "open_session should raise BrowserUnavailable when playwright is absent"
