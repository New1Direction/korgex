import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from src import korg_ledger as KL
from src.ledger_spec import verify_chain, verify_dag

def test_record_then_verify_chain_no_io():
    c = KL.InMemoryLedgerClient(source_agent="tester")
    s1 = c.record_user_prompt("hello")
    s2 = c.record_tool_call("Bash", {"command": "ls"}, {"out": "x"}, True, 5, triggered_by=s1)
    assert (s1, s2) == (1, 2)
    assert verify_chain(c.events) == []      # byte-integrity
    assert verify_dag(c.events) == []        # causal structure

def test_tamper_is_detected():
    c = KL.InMemoryLedgerClient(source_agent="tester")
    c.record_tool_call("Bash", {"command": "ls"}, {}, True, 0)
    c.events[0]["args"] = {"command": "rm -rf /"}   # forge after the fact
    assert verify_chain(c.events) != []             # the chain catches it

def test_conformance_with_local_journal(tmp_path):
    # same input + same key + same source_agent => byte-identical event (minus the file)
    key = b"k" * 32
    mem = KL.InMemoryLedgerClient(source_agent="tester", key=key)
    loc = KL.LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"), source_agent="tester")
    loc._key = key  # pin the key so hashes match
    mem.record_tool_call("Bash", {"command": "ls"}, {"out": "ok"}, True, 7)
    loc.record_tool_call("Bash", {"command": "ls"}, {"out": "ok"}, True, 7)
    import json
    disk = [json.loads(l) for l in (tmp_path / "j.jsonl").read_text().splitlines() if l.strip()]
    assert mem.events[0] == disk[0]   # identical: same redact/content-ref/body/chain

def test_conformance_llm_call_with_local_journal(tmp_path):
    # same input + same key + same source_agent => byte-identical llm_inference event
    key = b"k" * 32
    mem = KL.InMemoryLedgerClient(source_agent="tester", key=key)
    loc = KL.LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"), source_agent="tester")
    loc._key = key  # pin the key so hashes match
    kwargs = dict(
        model="gpt-4",
        prompt_tokens=20,
        completion_tokens=10,
        duration_ms=250,
        triggered_by=None,
        assistant_text="hello",
        cache_read_tokens=5,
        cache_creation_tokens=3,
        uncached_input_tokens=12,
    )
    mem.record_llm_call(**kwargs)
    loc.record_llm_call(**kwargs)
    import json
    disk = [json.loads(l) for l in (tmp_path / "j.jsonl").read_text().splitlines() if l.strip()]
    assert mem.events[0] == disk[0]   # cache fields must not be dropped


def test_seq_return():
    c = KL.InMemoryLedgerClient(source_agent="tester")
    s1 = c.record_user_prompt("prompt")
    s2 = c.record_llm_call(model="gpt-4", prompt_tokens=10, completion_tokens=5,
                            duration_ms=100, triggered_by=s1)
    s3 = c.record_tool_call("Read", {"file_path": "x.py"}, {"content": "ok"}, True, 3,
                             triggered_by=s2)
    assert s1 == 1
    assert s2 == 2
    assert s3 == 3
    assert len(c.events) == 3
