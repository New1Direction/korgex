"""Import a witness tool-dispatch journal into a korg-ledger@v1 chain.

The witness journal format already carries causal lineage (`parent_id`) and optional
artifact content-hashing, but is NOT a tamper-evident chain — any line can be edited,
deleted, or reordered undetectably. This adapter replays it into a hash-chained
journal so a session becomes verifiable, replayable evidence.
"""
from __future__ import annotations

import json

from src import import_adapters as IA
from src import ledger_spec as S

# Three witness events (one JSON object per line), chained by parent_id.
SYNTHETIC = [
    {"id": "ev-1", "timestamp": "2026-05-29T10:00:00+00:00", "timestamp_unix": 1764408000,
     "tool": "fetch", "action": "download", "target": "dataset.jsonl", "artifact": "/t/data",
     "artifact_hash": "a" * 64, "metadata": {"rows": 1280}, "parent_id": None,
     "agent": "worker", "hostname": "node-1"},
    {"id": "ev-2", "timestamp": "2026-05-29T10:01:00+00:00", "timestamp_unix": 1764408060,
     "tool": "transform", "action": "dedupe", "target": "dataset.jsonl", "artifact": None,
     "artifact_hash": None, "metadata": {"dropped": 100}, "parent_id": "ev-1",
     "agent": "worker", "hostname": "node-1"},
    {"id": "ev-3", "timestamp": "2026-05-29T10:02:00+00:00", "timestamp_unix": 1764408120,
     "tool": "report", "action": "write", "target": None, "artifact": "/t/out.csv",
     "artifact_hash": "b" * 64, "metadata": {}, "parent_id": "ev-2",
     "agent": "worker", "hostname": "node-1"},
]


def _write_journal(tmp_path) -> str:
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in SYNTHETIC) + "\n")
    return str(p)


def test_witness_is_a_registered_vendor():
    assert "witness" in IA.ADAPTERS


def test_witness_import_produces_a_verifiable_chain(tmp_path):
    out = str(tmp_path / "session.korg.jsonl")
    summary = IA.import_transcript(_write_journal(tmp_path), vendor="witness", out_path=out)
    assert summary["events"] == 3
    assert summary["verified"] is True
    events = [json.loads(ln) for ln in open(out) if ln.strip()]
    assert S.verify_chain(events) == [] and S.verify_dag(events) == []


def test_witness_reconstructs_causal_lineage_from_parent_id(tmp_path):
    out = str(tmp_path / "session.korg.jsonl")
    IA.import_transcript(_write_journal(tmp_path), vendor="witness", out_path=out)
    events = [json.loads(ln) for ln in open(out) if ln.strip()]
    assert "triggered_by" not in events[0]
    assert events[1]["triggered_by"] == 1
    assert events[2]["triggered_by"] == 2


def test_witness_preserves_tool_and_artifact_provenance(tmp_path):
    out = str(tmp_path / "session.korg.jsonl")
    IA.import_transcript(_write_journal(tmp_path), vendor="witness", out_path=out)
    events = [json.loads(ln) for ln in open(out) if ln.strip()]
    assert events[0]["tool_name"] == "fetch"
    assert events[0]["args"]["action"] == "download"
    assert events[0]["result"]["artifact_hash"] == "a" * 64  # provenance carried through


def test_witness_chain_catches_tampering(tmp_path):
    out = str(tmp_path / "session.korg.jsonl")
    IA.import_transcript(_write_journal(tmp_path), vendor="witness", out_path=out)
    events = [json.loads(ln) for ln in open(out) if ln.strip()]
    events[1]["args"]["action"] = "rewrite"  # someone doctors a step
    errs = S.verify_chain(events)
    assert errs and any("2" in str(e) for e in errs)
