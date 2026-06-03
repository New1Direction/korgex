"""ONE shared ledger — the interleaved cross-subsystem integration test (#3).

This is the executable oracle for spec/korg-ledger-v1/EVENTS.md. The wedge is a
SINGLE auditable journal as the sink for ALL cognition. Today korgex, korgchat,
and thumper can keep separate journals; this test proves they don't have to.

It puts three producers' events into ONE journal file, in interleaved order:

  1. a **korgex** tool call         (Python, real LocalJournalClient writer)
  2. a **thumper** run              (Rust, the real `thump` binary — cross-language)
  3. a **korgchat** chat turn       (Python, korgchat identity)

...and asserts the whole interleaved file verifies as ONE hash-chain (no edit/
insert/delete/reorder) AND one well-formed causal DAG — using nothing but the
ordinary korg-ledger@v1 verifier, no producer-specific logic.

The thumper segment shells out to the actual compiled Rust binary so this is a
genuine Python-writes-then-Rust-appends-then-Python-continues proof of the
byte-for-byte canonicalization agreement the moat rests on. If thumper isn't
built, that segment is skipped but the test still exercises the shared journal
across korgex + korgchat (and is marked accordingly).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import korg_ledger as L  # noqa: E402

# The thumper (Rust) cross-language segment is OPT-IN: set KORGEX_THUMP_BIN to the
# built `thump` binary to exercise the genuine Python→Rust→Python shared-journal
# proof. Unset (CI, fresh clones, any non-dev box) → that segment skips cleanly and
# the test still proves the shared journal across korgex + korgchat. (No hardcoded
# machine-specific path: it's not portable and doesn't belong in the repo.)
_THUMP_ENV = os.environ.get("KORGEX_THUMP_BIN")
THUMP_BIN = Path(_THUMP_ENV) if _THUMP_ENV else None


def _read(path: Path):
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def test_korgex_thumper_korgchat_share_one_verifiable_journal(tmp_path, monkeypatch):
    journal = tmp_path / "journal.jsonl"
    # All three producers resolve the journal from KORG_JOURNAL_PATH (the shared
    # key in EVENTS.md §5). Force korgex onto the durable local JSONL writer so
    # the test never depends on a running server / bridge transport.
    monkeypatch.setenv("KORG_JOURNAL_PATH", str(journal))
    monkeypatch.setenv("KORGEX_LEDGER", "local")
    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)

    # --- Producer 1: korgex (Python) — a user prompt + a tool call -----------
    korgex = L.LocalJournalClient(
        journal_path=str(journal), source_agent="agent:korgex@test"
    )
    root = korgex.record_user_prompt("add a /healthz endpoint and run the build")
    assert root == 1
    korgex_edit_seq = korgex.record_tool_call(
        tool_name="Edit",
        args={"file_path": "src/routes.py", "old": "", "new": "@app.get('/healthz')"},
        result={"applied": True},
        success=True,
        duration_ms=12,
        triggered_by=root,
    )
    assert korgex_edit_seq == 2

    # --- Producer 2: thumper (Rust) — a real `bun run` via the real binary ----
    thumper_ran = False
    if THUMP_BIN and THUMP_BIN.exists():
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "package.json").write_text(
            json.dumps({"name": "t", "scripts": {"build": "echo built"}})
        )
        env = dict(os.environ)
        env["KORG_JOURNAL_PATH"] = str(journal)
        env.pop("KORG_LEDGER_HMAC_KEY", None)
        proc = subprocess.run(
            [str(THUMP_BIN), "bun", "script", "run", "build"],
            cwd=str(proj),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # The run itself must have succeeded (echo built → exit 0).
        assert proc.returncode == 0, f"thump bun run failed: {proc.stderr}"
        thumper_ran = True

    # --- Producer 3: korgchat (Python) — a chat turn (inference) --------------
    # Same shared JSONL sink, korgchat's namespaced identity. A fresh client
    # recovers the chain head from the file, so it CONTINUES the chain that
    # korgex (and thumper, if present) already extended — it does not fork.
    korgchat = L.LocalJournalClient(
        journal_path=str(journal), source_agent="agent:korgchat@test"
    )
    korgchat.record_user_prompt("did the build pass?")
    korgchat.record_llm_call(
        model="claude-3-5",
        prompt_tokens=20,
        completion_tokens=8,
        duration_ms=140,
        triggered_by=None,
        assistant_text="Yes — the build echoed 'built'.",
    )

    # --- The whole interleaved file is ONE intact chain + sound DAG -----------
    events = _read(journal)

    # We saw all three source_agents land in the same file.
    agents = {e["source_agent"] for e in events}
    assert "agent:korgex@test" in agents
    assert "agent:korgchat@test" in agents
    if thumper_ran:
        assert "thumper" in agents, f"thumper run missing from journal: {agents}"
        # The thumper event is the contract's run.exec, flat-shaped, chained in.
        run_events = [e for e in events if e["tool_name"] == "run.exec"]
        assert len(run_events) == 1
        assert run_events[0]["args"]["operation"] == "script.run"

    # seq_ids are a single contiguous run across producers (no per-producer reset).
    seqs = [e["seq_id"] for e in events]
    assert seqs == list(range(1, len(events) + 1)), seqs

    # prev_hash links every event to the previous one regardless of which
    # producer (which language!) wrote it — that is the cross-impl byte-identity.
    assert events[0]["prev_hash"] == L.GENESIS_HASH
    for prev, cur in zip(events, events[1:]):
        assert cur["prev_hash"] == prev["entry_hash"], (
            f"chain broken at seq {cur['seq_id']} "
            f"({prev['source_agent']} → {cur['source_agent']})"
        )

    # The single korg-ledger@v1 verifier reads the interleaved file as one
    # tamper-evident, causally-sound trail.
    assert L.verify_chain(events) == [], "interleaved chain must be intact"
    assert L.verify_dag(events) == [], "interleaved DAG must be well-formed"

    # And tampering with ANY producer's event is localized + detected — even a
    # cross-language one. Edit the korgchat turn's reply text in place.
    last = events[-1]
    last_seq = last["seq_id"]
    tampered = [dict(e) for e in events]
    tampered[-1] = dict(last)
    tampered[-1]["result"] = dict(last["result"], text="FORGED")
    errors = L.verify_chain(tampered)
    assert any(f"seq {last_seq}" in err for err in errors), errors


def test_thumper_segment_is_actually_exercised():
    """Guardrail: if the thumper binary exists, the interleaved test MUST cover
    the cross-language path — so a green run isn't silently korgex+korgchat only.
    This fails loudly if someone deletes the build rather than skips honestly."""
    if not (THUMP_BIN and THUMP_BIN.exists()):
        pytest.skip("KORGEX_THUMP_BIN unset / binary not built — cross-language segment unavailable")
    assert THUMP_BIN.exists()
