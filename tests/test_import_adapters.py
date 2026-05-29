"""
Cross-vendor ledger import adapters (roadmap idea #7).

"Cross-vendor" is the wedge competitors structurally can't ship — their memory
is their lock-in. These adapters ingest another vendor's session transcript
(Claude Code JSONL to start) and re-emit it as a korg-ledger@v1 chained journal:
one inspectable, verifiable local artifact that any korg verifier validates.
Once any vendor's session replays into the chained ledger, korg stops being
"another agent" and becomes the neutral audit substrate UNDER all of them.

The output MUST verify under the same ledger_spec the rest of the ecosystem
shares (verify_chain + verify_dag), with causal triggered_by links reconstructed
from the transcript's parent pointers.
"""

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import import_adapters as IA  # noqa: E402
from src import ledger_spec as S  # noqa: E402


# A minimal but representative Claude Code transcript: metadata noise, a user
# prompt, an assistant turn with text + a tool_use, and a tool_result.
CC_LINES = [
    {"type": "mode", "mode": "default", "sessionId": "s"},  # noise → skipped
    {"type": "user", "uuid": "u1", "parentUuid": None, "timestamp": "2026-05-29T00:00:00Z",
     "message": {"role": "user", "content": "add a function to mathx.py"}},
    {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": "2026-05-29T00:00:01Z",
     "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [
         {"type": "text", "text": "I'll create it."},
         {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "mathx.py"}},
     ]}},
    {"type": "user", "uuid": "u2", "parentUuid": "a1", "timestamp": "2026-05-29T00:00:02Z",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
     ]}},
]


def _events(source_agent="claude-code"):
    return IA.to_ledger_events(IA.parse_claude_code(CC_LINES), source_agent=source_agent)


def test_parse_skips_noise_and_orders_actions():
    actions = IA.parse_claude_code(CC_LINES)
    ops = [a["op"] for a in actions]
    # user prompt → assistant llm turn → its tool_use
    assert ops == ["user_prompt", "llm_inference", "tool_call"]
    assert actions[2]["payload"]["tool_name"] == "Write"


def test_events_are_chained_and_dag_sound():
    events = _events()
    assert [e["tool_name"] for e in events] == ["user_prompt", "llm_inference", "Write"]
    assert all(e["source_agent"] == "claude-code" for e in events)
    assert events[0]["prev_hash"] == S.GENESIS_HASH
    # the produced journal verifies under the shared spec
    assert S.verify_chain(events) == []
    assert S.verify_dag(events) == []


def test_causal_links_reconstructed_from_parent_pointers():
    events = _events()
    by_seq = {e["seq_id"]: e for e in events}
    llm = next(e for e in events if e["tool_name"] == "llm_inference")
    prompt = next(e for e in events if e["tool_name"] == "user_prompt")
    tool = next(e for e in events if e["tool_name"] == "Write")
    # assistant turn was triggered by the user prompt; the tool by the assistant turn
    assert llm["triggered_by"] == prompt["seq_id"]
    assert tool["triggered_by"] == llm["seq_id"]
    # every triggered_by is strictly earlier (DAG soundness)
    assert all(by_seq[e["seq_id"]]["seq_id"] > e["triggered_by"]
               for e in events if e.get("triggered_by") is not None)


def test_import_transcript_writes_a_verifiable_journal(tmp_path):
    src = tmp_path / "session.jsonl"
    src.write_text("\n".join(json.dumps(l) for l in CC_LINES) + "\n")
    out = tmp_path / "imported.jsonl"
    summary = IA.import_transcript(str(src), vendor="claude-code", out_path=str(out))
    assert summary["events"] == 3
    assert summary["source_agent"] == "claude-code"

    events = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert len(events) == 3
    assert S.verify_chain(events) == [] and S.verify_dag(events) == []


def test_unknown_vendor_rejected(tmp_path):
    src = tmp_path / "x.jsonl"; src.write_text("{}\n")
    with pytest.raises(ValueError):
        IA.import_transcript(str(src), vendor="nope", out_path=str(tmp_path / "o.jsonl"))
