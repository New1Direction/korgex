"""Tests for the shareable, self-verifying HTML audit report (`korgex audit --html`).

The keystone test runs the report's *embedded* JS verifier in node against the
frozen korg-ledger@v1 conformance vectors and asserts it reproduces the frozen
tip — proving the in-browser verifier and the Python chain agree byte-for-byte.
A skeptic opening the report in their own browser is running the same algorithm.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VECTORS = REPO / "spec" / "korg-ledger-v1" / "vectors"
CONFORMANCE = REPO / "spec" / "korg-ledger-v1" / "conformance.json"
JS_ASSET = REPO / "src" / "assets" / "korg_verify.js"

FROZEN_BASIC_TIP = "7418b9105a664e21078fde881fbd8a5295c49bb384aa62a48d5b544292d910af"


def _load_vector(name: str) -> list:
    with open(VECTORS / name) as f:
        return [json.loads(line) for line in f if line.strip()]


def _run_js_verifier(events: list) -> dict:
    """Run the embedded JS verifier in node over `events`; return {computedTip, errors}."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to exercise the in-browser verifier")
    driver = textwrap.dedent(
        f"""
        const v = require({json.dumps(str(JS_ASSET))});
        const events = {json.dumps(events)};
        (async () => {{
          const errors = await v.verifyChain(events);
          let computedTip = v.GENESIS;
          for (const e of events) computedTip = await v.chainHash(e);
          process.stdout.write(JSON.stringify({{ computedTip, errors }}));
        }})();
        """
    )
    out = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


def test_embedded_verifier_reproduces_frozen_tip():
    """The exact JS embedded in the report must reproduce the frozen basic-intact tip."""
    result = _run_js_verifier(_load_vector("basic-intact.jsonl"))
    assert result["computedTip"] == FROZEN_BASIC_TIP
    assert result["errors"] == [], "an intact chain must verify with no errors"


def test_embedded_verifier_catches_tampering():
    """The in-browser verifier must localize a tampered event (the visceral proof)."""
    result = _run_js_verifier(_load_vector("tampered-content.jsonl"))
    assert result["errors"], "tampered vector must produce at least one error"
    assert any(e.get("seq") == 2 for e in result["errors"]), result["errors"]


def test_embedded_verifier_rejects_regenerated_chain_against_anchored_tip():
    """A fully regenerated forgery (edit a body, then re-link + re-hash the whole
    chain so it is internally consistent) slips past the naive check but MUST be
    caught by the JS verifier when an anchored tip is supplied. This is the
    browser side of the $1k-bounty's safety: forging requires a second preimage."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to exercise the in-browser verifier")
    genuine = _load_vector("basic-intact.jsonl")
    genuine_tip = genuine[-1]["entry_hash"]
    driver = textwrap.dedent(
        f"""
        const v = require({json.dumps(str(JS_ASSET))});
        const events = {json.dumps(genuine)};
        const GEN = {json.dumps(genuine_tip)};
        (async () => {{
          // forge: change the first event, then re-link + re-hash the entire chain
          events[0].args = Object.assign({{}}, events[0].args, {{ body: 'FORGED' }});
          let prev = v.GENESIS;
          for (const e of events) {{
            e.prev_hash = prev;
            delete e.entry_hash;
            e.entry_hash = await v.chainHash(e);
            prev = e.entry_hash;
          }}
          const naive = await v.verifyChain(events);          // internally consistent now
          const anchored = await v.verifyChain(events, GEN);  // anchored tip must catch it
          process.stdout.write(JSON.stringify({{ naive, anchored }}));
        }})();
        """
    )
    out = subprocess.run([node, "-e", driver], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    res = json.loads(out.stdout)
    assert res["naive"] == [], "regenerated chain is internally consistent — naive check passes"
    assert res["anchored"], "anchored tip must reject the regenerated forgery"
    assert any("tip" in (e.get("why") or "").lower() for e in res["anchored"]), res["anchored"]


def test_report_is_self_contained_and_embeds_events_and_verifier():
    from src.audit_report import render_html

    events = _load_vector("basic-intact.jsonl")
    html = render_html(events, {"session": "demo-session.jsonl", "vendor": "claude-code"})

    assert isinstance(html, str) and "<html" in html.lower()
    # self-contained: the verifier is inlined, not linked
    assert "verifyChain" in html and "chainHash" in html
    assert "<script src" not in html, "report must be a single self-contained file"
    # embeds every event (so the recipient's browser can re-verify locally)
    for e in events:
        assert e["entry_hash"] in html
    # the session name and a live tamper-test control are present
    assert "demo-session.jsonl" in html
    assert "tamper" in html.lower()
