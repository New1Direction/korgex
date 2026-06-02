"""Offline tests for SLICE 3 self-healing selectors.

Scrapling-style: fingerprint a targeted element; when the DOM drifts and its
exact backend_node_id is gone, relocate it by similarity. korgex's NOVEL framing
is that the relocation is a FIRST-CLASS, hash-chained ledger fact — a signed
'selector-drift' event — so verify_chain proves when/how a selector moved.

All pure/offline: fingerprinting + similarity scoring take plain dicts, and the
ledger is injected as a FakeLedger. No browser, no network.
"""
from __future__ import annotations

from src import browser as B


class FakeLedger:
    def __init__(self):
        self.calls = []

    def record_tool_call(self, tool_name, args, result, success, duration_ms,
                         triggered_by=None):
        self.calls.append({
            "tool_name": tool_name, "args": args, "result": result,
            "success": success, "duration_ms": duration_ms,
        })
        return len(self.calls)


def _snapshot_with(interactives):
    return {"url": "https://e.com/", "interactives": interactives}


def test_fingerprint_element_captures_stable_subset():
    fp = B.fingerprint_element({
        "tag": "button", "role": "button", "name": "Submit",
        "text": "Submit", "attrs": {"id": "go", "class": "btn primary"},
    })
    assert fp["tag"] == "button"
    assert fp["role"] == "button"
    assert fp["name"] == "Submit"
    # the id is part of the stable subset; transient stuff (backend_node_id) is not
    assert fp.get("id") == "go"
    assert "backend_node_id" not in fp


def test_relocate_finds_drifted_element_by_similarity():
    fp = B.fingerprint_element({"tag": "button", "role": "button",
                                "name": "Submit", "text": "Submit"})
    # NEW snapshot: the old node id is gone; a button 'Submit' now sits at index 2
    new = _snapshot_with([
        {"index": 0, "backend_node_id": 200, "tag": "a", "role": "link", "name": "Home"},
        {"index": 1, "backend_node_id": 201, "tag": "input", "role": "textbox", "name": "Search"},
        {"index": 2, "backend_node_id": 202, "tag": "button", "role": "button", "name": "Submit"},
    ])
    hit = B.relocate(fp, new)
    assert hit is not None
    assert hit["index"] == 2
    assert hit["backend_node_id"] == 202
    assert hit["similarity"] >= 0.7


def test_relocate_returns_none_below_threshold():
    fp = B.fingerprint_element({"tag": "button", "role": "button",
                                "name": "Checkout Now", "text": "Checkout Now"})
    new = _snapshot_with([
        {"index": 0, "backend_node_id": 9, "tag": "a", "role": "link", "name": "Privacy Policy"},
        {"index": 1, "backend_node_id": 10, "tag": "img", "role": "img", "name": "Logo banner"},
    ])
    assert B.relocate(fp, new, threshold=0.6) is None


def test_heal_and_record_emits_signed_drift_event_on_relocation():
    fp = B.fingerprint_element({"tag": "button", "role": "button",
                                "name": "Submit", "text": "Submit"})
    new = _snapshot_with([
        {"index": 2, "backend_node_id": 202, "tag": "button", "role": "button", "name": "Submit"},
    ])
    led = FakeLedger()
    res = B.heal_and_record(fp, new, _ledger=led)
    assert res is not None
    assert res["index"] == 2
    # exactly one 'selector-drift' ledger fact, carrying the drift evidence
    assert len(led.calls) == 1
    ev = led.calls[0]
    assert ev["tool_name"] == "selector-drift"
    assert ev["args"]["new_index"] == 2
    assert ev["args"]["new_backend_node_id"] == 202
    assert ev["args"]["old_fingerprint"]["name"] == "Submit"
    assert "similarity" in ev["args"]
    assert ev["result"]["relocated"] is True


def test_heal_and_record_records_nothing_when_relocation_fails():
    fp = B.fingerprint_element({"tag": "button", "role": "button",
                                "name": "Buy a yacht", "text": "Buy a yacht"})
    new = _snapshot_with([
        {"index": 0, "backend_node_id": 1, "tag": "a", "role": "link", "name": "About us"},
    ])
    led = FakeLedger()
    res = B.heal_and_record(fp, new, _ledger=led)
    assert res is None
    assert led.calls == []
