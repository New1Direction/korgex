"""Korgex artifact index — content-addressed tracking of what a run produces.

Every file or output a run/DAG-node produces is hashed (sha256) and indexed by that
hash, so identical content **dedups** to a single entry and you can ask "what did
node X produce?" or "where did this artifact come from?". Each *unique* artifact's
lineage is appended to the korg-ledger, so provenance is tamper-evident alongside
the rest of the run. Pairs with `exec_graph` (node → artifacts) and the ledger.

Hashing, dedup, and queries are pure and in-memory; the optional `journal_client`
adds the durable, verifiable lineage link.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class Artifact:
    id: str               # content hash (sha256 hex) — the address
    sha256: str
    size: int
    path: str | None = None
    produced_by: str | None = None
    metadata: dict = field(default_factory=dict)


def _to_bytes(content) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    raise TypeError("artifact content must be bytes or str")


class ArtifactIndex:
    """In-memory content-addressed index; optionally records lineage to a ledger."""

    def __init__(self, journal_client=None):
        self._by_id: dict = {}
        self._journal = journal_client

    def __len__(self) -> int:
        return len(self._by_id)

    def record(self, content, *, path=None, produced_by=None, metadata=None) -> str:
        """Index `content` (bytes or str). Returns its content-hash id. Identical
        content is deduped — the same id is returned and no new lineage is recorded."""
        data = _to_bytes(content)
        aid = hashlib.sha256(data).hexdigest()
        if aid in self._by_id:
            return aid
        art = Artifact(id=aid, sha256=aid, size=len(data),
                       path=str(path) if path is not None else None,
                       produced_by=produced_by, metadata=dict(metadata or {}))
        self._by_id[aid] = art
        self._record_lineage(art)
        return aid

    def record_file(self, path, *, produced_by=None, metadata=None) -> str:
        """Index a file by its content. The file's path is stored on the artifact."""
        with open(path, "rb") as f:
            data = f.read()
        return self.record(data, path=path, produced_by=produced_by, metadata=metadata)

    def get(self, artifact_id):
        """The Artifact for an id, or None."""
        return self._by_id.get(artifact_id)

    def by_producer(self, producer) -> list:
        """All artifacts produced by a given run/node id."""
        return [a for a in self._by_id.values() if a.produced_by == producer]

    def all(self) -> list:
        return list(self._by_id.values())

    def _record_lineage(self, art) -> None:
        if self._journal is None:
            return
        try:
            self._journal.record_tool_call(
                "artifact.recorded",
                {"artifact_id": art.id, "produced_by": art.produced_by,
                 "path": art.path, "size": art.size},
                {"ok": True}, True, 0)
        except Exception:
            pass  # lineage is best-effort; never break the producing run
