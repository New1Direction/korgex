"""Tests for the content-addressed artifact index (src/artifact_index.py).

Tracks every file/output a run produces, addressed by content hash so identical
content dedups to one entry, queryable by the run/node that produced it, with each
unique artifact's lineage appended to the korg-ledger. Hashing, dedup, and queries
are pure; the lineage link is verified against a real temp journal.
"""
import hashlib

from src.artifact_index import ArtifactIndex


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_record_returns_content_hash_id():
    idx = ArtifactIndex()
    aid = idx.record(b"hello world")
    assert aid == sha(b"hello world")


def test_record_accepts_str_and_bytes_equivalently():
    idx = ArtifactIndex()
    assert idx.record("hello") == idx.record(b"hello")
    assert len(idx) == 1


def test_same_content_dedups_to_one_entry():
    idx = ArtifactIndex()
    a = idx.record(b"same")
    b = idx.record(b"same")
    assert a == b
    assert len(idx) == 1


def test_different_content_gets_distinct_ids():
    idx = ArtifactIndex()
    idx.record(b"one")
    idx.record(b"two")
    assert len(idx) == 2


def test_record_file_hashes_file_content(tmp_path):
    p = tmp_path / "out.txt"
    p.write_bytes(b"file body")
    idx = ArtifactIndex()
    aid = idx.record_file(str(p))
    assert aid == sha(b"file body")
    art = idx.get(aid)
    assert art.size == len(b"file body")
    assert art.path == str(p)


def test_get_returns_artifact_metadata():
    idx = ArtifactIndex()
    aid = idx.record(b"payload", produced_by="node-a", metadata={"kind": "report"})
    art = idx.get(aid)
    assert art.size == len(b"payload")
    assert art.produced_by == "node-a"
    assert art.metadata == {"kind": "report"}


def test_get_unknown_returns_none():
    assert ArtifactIndex().get("deadbeef") is None


def test_by_producer_filters_to_that_producer():
    idx = ArtifactIndex()
    idx.record(b"a1", produced_by="node-a")
    idx.record(b"a2", produced_by="node-a")
    idx.record(b"b1", produced_by="node-b")
    assert len(idx.by_producer("node-a")) == 2
    assert len(idx.by_producer("node-b")) == 1


# ── Ledger lineage ──────────────────────────────────────────────────────────

def _events(journal_path):
    import json
    with open(journal_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_records_lineage_event_to_ledger(tmp_path):
    from src.korg_ledger import LocalJournalClient

    journal = str(tmp_path / "art.journal")
    client = LocalJournalClient(journal_path=journal, source_agent="korg:artifacts")
    idx = ArtifactIndex(journal_client=client)

    aid = idx.record(b"payload", produced_by="node-a")

    evs = [e for e in _events(journal) if e.get("tool_name") == "artifact.recorded"]
    assert len(evs) == 1
    assert evs[0]["args"]["artifact_id"] == aid
    assert evs[0]["args"]["produced_by"] == "node-a"


def test_dedup_does_not_double_record_in_ledger(tmp_path):
    from src.korg_ledger import LocalJournalClient

    journal = str(tmp_path / "art.journal")
    client = LocalJournalClient(journal_path=journal, source_agent="korg:artifacts")
    idx = ArtifactIndex(journal_client=client)

    idx.record(b"same")
    idx.record(b"same")  # identical content → no second lineage event

    evs = [e for e in _events(journal) if e.get("tool_name") == "artifact.recorded"]
    assert len(evs) == 1
