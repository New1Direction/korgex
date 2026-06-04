"""Fast, better-ranked recall via SQLite FTS5 (BM25) over the ledger events.

The default substring scorer ranks by raw term-occurrence and requires every term to
appear — brittle, since a multi-term query can match nothing. This builds a transient
in-memory FTS5 index over the events and ranks by BM25: proper relevance, partial matches
allowed, path/identifier tokens split by the unicode61 tokenizer. It runs on the Python
stdlib (FTS5 is built into sqlite3) — no new dependency, no network.

A persistent sqlite-vec / vec0 semantic store is a natural extension, but it needs a Python
whose sqlite3 can load extensions (macOS system Python can't), so it stays opt-in / elsewhere.
korgex's existing optional fastembed cosine path (recall.search mode="semantic") covers
semantic ranking when that's wanted.
"""
from __future__ import annotations

import re
import sqlite3

from src import recall as _recall

_WORD = re.compile(r"[A-Za-z0-9]+")


def fts_available() -> bool:
    """True if this interpreter's sqlite3 has FTS5 (it almost always does)."""
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        conn.close()
        return True
    except sqlite3.Error:
        return False


def _match_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression: alphanumeric terms, each quoted
    (so user punctuation can't inject FTS5 operators), OR-joined so it ranks by BM25 rather
    than demanding every term. Empty string if there are no usable terms."""
    terms = _WORD.findall(query or "")
    return " OR ".join(f'"{t}"' for t in terms)


def search_fts(events: list, query: str, *, top_n: int = 20) -> list | None:
    """Rank `events` against `query` with FTS5 BM25. Returns ``[{event, score}]`` (higher
    score = better, matching recall.search's contract), ``[]`` on no match, or ``None`` if
    FTS5 isn't available so the caller can fall back to substring search."""
    if not fts_available():
        return None
    match = _match_query(query)
    if not match:
        return []
    by_row: dict[int, dict] = {}
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE docs USING fts5(body)")
        for i, ev in enumerate(events or []):
            rowid = i + 1
            conn.execute("INSERT INTO docs(rowid, body) VALUES (?, ?)",
                         (rowid, _recall.event_text(ev)))
            by_row[rowid] = ev
        rows = conn.execute(
            "SELECT rowid, bm25(docs) AS score FROM docs WHERE docs MATCH ? "
            "ORDER BY score LIMIT ?",
            (match, int(top_n)),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    # bm25() is lower=better; negate so higher=better (same direction as the substring scorer)
    return [{"event": by_row[rid], "score": -float(score)} for rid, score in rows]
