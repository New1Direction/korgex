"""Verifiable training trajectories from korg-ledger@v1 journals.

A korgex run is already a tamper-evident chain; this turns it into a normalized
(ShareGPT-style) training trajectory stamped with its source's provenance — so the
training data carries proof it came from an unaltered run. Because the source is
hash-chained, a poisoned/edited trajectory is detectable (verified=False) — a
built-in poisoning defense. Export is append-only (never-delete): a flywheel.
"""
from __future__ import annotations

import json

from src import import_adapters as IA
from src.trajectory import export_trajectory, to_trajectory


def _journal() -> list:
    actions = [
        {"op": "user_prompt", "uuid": "u", "parent_uuid": None,
         "payload": {"prompt": "fix the parser bug"}},
        {"op": "llm_inference", "uuid": "a", "parent_uuid": "u",
         "payload": {"model": "m", "text": "I'll guard the empty case and add a test."}},
        {"op": "tool_call", "uuid": "t", "parent_uuid": "a",
         "payload": {"tool_name": "Edit", "args": {"file_path": "parse.py"}}},
    ]
    return IA.to_ledger_events(actions, source_agent="korgex")


def test_trajectory_maps_turns_in_sharegpt_form():
    conv = to_trajectory(_journal())["conversations"]
    assert conv[0] == {"from": "human", "value": "fix the parser bug"}
    assert conv[1]["from"] == "gpt" and "guard" in conv[1]["value"].lower()
    assert conv[2]["from"] == "tool" and "Edit" in conv[2]["value"]


def test_trajectory_carries_verifiable_provenance():
    p = to_trajectory(_journal())["provenance"]
    assert p["spec"] == "korg-ledger@v1"
    assert p["verified"] is True
    assert p["events"] == 3 and len(p["tip_hash"]) == 64


def test_trajectory_marks_a_tampered_source_unverified():
    events = _journal()
    events[1]["result"]["text"] = "poisoned"  # edit without re-hashing
    assert to_trajectory(events)["provenance"]["verified"] is False


def test_trajectory_excludes_meta_audit_events():
    events = _journal()
    events.append({
        "schema_version": "1.0", "seq_id": 99, "source_agent": "korgex",
        "tool_name": "edit_policy", "args": {}, "result": {"action": "allow"},
        "success": True, "duration_ms": 0, "prev_hash": events[-1]["entry_hash"],
    })
    conv = to_trajectory(events)["conversations"]
    assert all("edit_policy" not in t["value"] for t in conv)
    assert len(conv) == 3  # human + gpt + tool, no meta turn


def test_export_is_append_only_and_accumulates(tmp_path):
    jf = tmp_path / "j.jsonl"
    jf.write_text("\n".join(json.dumps(e) for e in _journal()) + "\n")
    out = str(tmp_path / "trajectories.jsonl")
    s1 = export_trajectory(str(jf), out)
    s2 = export_trajectory(str(jf), out)  # second export must NOT overwrite
    lines = [ln for ln in open(out) if ln.strip()]
    assert len(lines) == 2  # never-delete flywheel
    assert s1["turns"] == 3 and s2["verified"] is True
