"""Offline tests for SLICE 2 stealth wiring: the opt-in Patchright driver.

Stealth is RECORDED POLICY, never hidden — the chosen driver is stamped on the
session and therefore on every verifiable trace. These tests prove:
  • open_session(stealth=True) imports 'patchright.sync_api' (not 'playwright')
    and stamps driver='patchright'; stealth=False imports 'playwright.sync_api'.
  • resolve_stealth() honors an explicit arg (wins), the KORGEX_BROWSER_STEALTH
    env var, and a config flag.
  • A stealth session threads driver='patchright' into the act-trace dict.

No real browser and no patchright install: importlib.import_module is
monkeypatched to hand back a FAKE playwright-shaped module whose CDP session is
a FakeCDP, and to RECORD which module name was requested.
"""
from __future__ import annotations

import importlib

from src import browser as B
from src import tools_impl as T


class FakeCDP:
    def __init__(self, responses=None):
        self._r = responses or {}
        self.calls = []
        self.detached = False

    def send(self, method, params=None):
        self.calls.append((method, params))
        return self._r.get(method, {})

    def detach(self):
        self.detached = True


# A minimal fake of the `<driver>.sync_api` module surface open_session touches:
# sync_playwright().start().chromium.launch().new_page().context.new_cdp_session()
class _FakeCtx:
    def new_cdp_session(self, page):
        return FakeCDP({})


class _FakePage:
    def __init__(self):
        self.context = _FakeCtx()
        self.url = "https://example.com/"

    def goto(self, url, **kw):
        self.url = url

    def screenshot(self, **kw):
        return b""


class _FakeBrowser:
    def new_page(self):
        return _FakePage()


class _FakeChromium:
    def launch(self, headless=True):
        return _FakeBrowser()


class _FakePW:
    chromium = _FakeChromium()


class _FakeSyncPW:
    def start(self):
        return _FakePW()


class _FakeSyncApiModule:
    def sync_playwright(self):
        return _FakeSyncPW()


def _patch_import(monkeypatch):
    """Patch importlib.import_module to record the requested driver module name
    and return a fake sync_api module for playwright/patchright."""
    requested = {}
    real = importlib.import_module

    def fake_import(name, *a, **k):
        if name.startswith("playwright") or name.startswith("patchright"):
            requested["name"] = name
            return _FakeSyncApiModule()
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    return requested


# ── Step 1: stealth flag selects patchright and is recorded ───────────────────

def test_open_session_stealth_uses_patchright_and_records_driver(monkeypatch):
    requested = _patch_import(monkeypatch)
    sess = B.open_session(stealth=True)
    assert requested["name"] == "patchright.sync_api"
    assert sess.driver == "patchright"


def test_open_session_default_uses_playwright(monkeypatch):
    requested = _patch_import(monkeypatch)
    sess = B.open_session(stealth=False)
    assert requested["name"] == "playwright.sync_api"
    assert sess.driver == "playwright"


def test_resolve_stealth_explicit_arg_wins(monkeypatch):
    # explicit beats both env and config
    monkeypatch.setenv("KORGEX_BROWSER_STEALTH", "1")
    assert B.resolve_stealth(explicit=False, config={"browser_stealth": True}) is False
    assert B.resolve_stealth(explicit=True) is True


def test_resolve_stealth_env_var(monkeypatch):
    monkeypatch.setenv("KORGEX_BROWSER_STEALTH", "1")
    assert B.resolve_stealth() is True
    monkeypatch.setenv("KORGEX_BROWSER_STEALTH", "yes")
    assert B.resolve_stealth() is True
    monkeypatch.setenv("KORGEX_BROWSER_STEALTH", "0")
    assert B.resolve_stealth() is False
    monkeypatch.delenv("KORGEX_BROWSER_STEALTH", raising=False)
    assert B.resolve_stealth() is False


def test_resolve_stealth_config_flag(monkeypatch):
    monkeypatch.delenv("KORGEX_BROWSER_STEALTH", raising=False)
    assert B.resolve_stealth(config={"browser_stealth": True}) is True
    assert B.resolve_stealth(config={"browser_stealth": False}) is False
    assert B.resolve_stealth(config={}) is False


# ── stealth is RECORDED on the trace (driver field), never hidden ─────────────

SLICE1_DOM = {
    "DOM.getDocument": {
        "root": {
            "backendNodeId": 1, "nodeName": "BODY", "attributes": [],
            "children": [
                {"backendNodeId": 42, "nodeName": "BUTTON", "attributes": [], "children": []},
            ],
        }
    },
    "Accessibility.getFullAXTree": {
        "nodes": [
            {"backendDOMNodeId": 42, "role": {"value": "button"},
             "name": {"value": "Submit"}, "ignored": False},
        ]
    },
    "DOM.getBoxModel": {"model": {"content": [10, 10, 30, 10, 30, 20, 10, 20]}},
}


def test_stealth_driver_is_recorded_on_act_trace():
    sess = B.BrowserSession(client=FakeCDP(SLICE1_DOM), page=_FakePage(), driver="patchright")
    sess.snapshot()
    out = T.tool_browser_click(index=0, _session=sess)
    assert out["ok"] is True
    assert out["driver"] == "patchright"  # stealth is recorded policy, never hidden
