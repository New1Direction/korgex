"""
recall.py — the READ side of the korg ledger for korgex (roadmap P2).

korgex spent three features writing a causal journal that nothing read back.
This module lets the agent recall its own past — semantic/substring search over
the ledger korgex already writes — and, crucially, RECONCILES recalled file
references against the live workspace so the agent trusts current state over
stale memory (the "trust-hierarchy" problem incumbents punt on). The content-
addressed blob store korgex already uses (sha256) makes drift an exact signal.

Recall is provider-free and dependency-light: substring (AND-of-terms) always
works; semantic ranking via fastembed is used only if it's importable.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _journal_path(repo_root: str = None) -> str:
    """Where korgex's ledger lives. KORG_JOURNAL_PATH wins, else .korg/journal.json."""
    env = os.environ.get("KORG_JOURNAL_PATH")
    if env:
        return env
    return str(Path(repo_root or os.getcwd()) / ".korg" / "journal.json")


def load_events(journal_path: str) -> list:
    """Load + normalize ledger events from a JSON array or JSONL file.

    Tolerant of two shapes: a flat event dict (korgex/recall-mcp style), or a
    registry JournalEvent ({seq_id, metadata, event:{...}}) — the latter is
    best-effort flattened. Missing/malformed file → [].
    """
    p = Path(journal_path)
    if not p.exists():
        return []
    raw = p.read_text().strip()
    if not raw:
        return []

    objs: list = []
    # JSON array first; fall back to JSONL.
    try:
        data = json.loads(raw)
        objs = data if isinstance(data, list) else [data]
    except (json.JSONDecodeError, ValueError):
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                objs.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue

    return [_normalize(o) for o in objs if isinstance(o, dict)]


def _normalize(obj: dict) -> dict:
    """Normalize an on-disk event to {seq_id, tool_name, args, result, success}."""
    if "tool_name" in obj:
        src = obj
    elif isinstance(obj.get("event"), dict):
        # registry JournalEvent: dig for the AgentToolCall payload, best-effort
        ev = obj["event"]
        src = ev.get("AgentToolCall", ev) if isinstance(ev, dict) else {}
        src = {**src, "seq_id": obj.get("seq_id")}
    else:
        src = obj
    return {
        "seq_id": src.get("seq_id"),
        "tool_name": src.get("tool_name", ""),
        "args": src.get("args", {}),
        "result": src.get("result", {}),
        "success": src.get("success", True),
        "triggered_by": src.get("triggered_by"),
    }


def event_text(event: dict) -> str:
    """Signal-dense, searchable text for an event: tool + args + any reply text."""
    parts = [str(event.get("tool_name", ""))]
    args = event.get("args")
    if args:
        parts.append(json.dumps(args, default=str))
    result = event.get("result")
    if isinstance(result, dict):
        if isinstance(result.get("text"), str):
            parts.append(result["text"])
        else:
            parts.append(json.dumps(result, default=str))
    elif result:
        parts.append(str(result))
    return " ".join(parts)


def search(events: list, query: str, top_n: int = 5, mode: str = "auto") -> list:
    """Rank events against a query. Returns [{event, score}] (highest first).

    Substring mode (default) requires ALL query terms to appear (AND-of-terms), ranked by
    total term occurrences, recency (seq_id) as tiebreak. mode="fts" ranks by SQLite FTS5
    BM25 (relevance, partial matches, no new dependency), falling back to substring if FTS5
    is absent. mode="semantic" uses fastembed cosine when importable; otherwise substring.
    """
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        return []

    if mode == "fts":
        from src import recall_index as RI
        indexed = RI.search_fts(events, query, top_n=top_n)
        if indexed is not None:
            return indexed
        # FTS5 unavailable on this interpreter → fall through to substring

    if mode == "semantic":
        semantic = _semantic_search(events, query, top_n)
        if semantic is not None:
            return semantic

    scored = []
    for ev in events:
        text = event_text(ev).lower()
        if all(t in text for t in terms):
            occurrences = sum(text.count(t) for t in terms)
            scored.append({"event": ev, "score": occurrences})

    scored.sort(key=lambda h: (h["score"], h["event"].get("seq_id") or 0), reverse=True)
    return scored[:top_n]


def _semantic_search(events: list, query: str, top_n: int):
    """Cosine ranking via fastembed; returns None if the dep isn't available."""
    try:
        from fastembed import TextEmbedding
    except Exception:
        return None
    model = TextEmbedding()
    texts = [event_text(e) for e in events]
    if not texts:
        return []
    import numpy as np
    vecs = list(model.embed(texts + [query]))
    qv = vecs[-1]
    out = []
    for ev, v in zip(events, vecs[:-1]):
        denom = (np.linalg.norm(v) * np.linalg.norm(qv)) or 1.0
        out.append({"event": ev, "score": float(np.dot(v, qv) / denom)})
    out.sort(key=lambda h: h["score"], reverse=True)
    return out[:top_n]


def expand_causal(events: list, seeds: list, *, depth: int = 1,
                  direction: str = "both", max_total: int = None) -> list:
    """Expand seed events along the causal DAG (`triggered_by`) up to `depth` hops.
    Returns seeds + neighbors as a deduped event list — seeds first, then nearest
    neighbors — optionally capped at `max_total`.

    `direction` selects which edges to follow:
      - ``"causes"``  — only the event that triggered each seed (the "why"). One cause per
        event, so it never drags in unrelated siblings — the safe choice for per-step
        context.
      - ``"effects"`` — only the events each seed triggered (the "what happened"). A broad
        prompt can fan out to many, so bound it.
      - ``"both"``    — both (default).

    This is the causal half of retrieval, the part flat text search over a non-causal
    store can't do: a matched action brings the prompt that caused it, a matched prompt
    brings the actions it triggered.
    """
    want_causes = direction in ("both", "causes")
    want_effects = direction in ("both", "effects")

    by_seq: dict = {}
    children: dict = {}
    for e in events or []:
        s = e.get("seq_id")
        if s is not None:
            by_seq[s] = e
        tb = e.get("triggered_by")
        if tb is not None:
            children.setdefault(tb, []).append(e)

    seen: dict = {}

    def _add(e) -> bool:
        s = e.get("seq_id")
        key = s if s is not None else id(e)
        if key in seen:
            return False
        seen[key] = e
        return True

    for e in seeds or []:
        _add(e)

    frontier = list(seeds or [])
    for _ in range(max(0, depth)):
        nxt = []
        for e in frontier:
            if want_causes:
                tb = e.get("triggered_by")
                if tb is not None and tb in by_seq and _add(by_seq[tb]):
                    nxt.append(by_seq[tb])
            if want_effects:
                for ch in children.get(e.get("seq_id"), []):
                    if _add(ch):
                        nxt.append(ch)
        frontier = nxt
        if not frontier:
            break

    out = list(seen.values())
    if max_total is not None and len(out) > max_total:
        out = out[:max_total]
    return out


def _event_sim(e1: dict, e2: dict) -> float:
    """Token-Jaccard similarity of two events' searchable text, in [0,1]."""
    t1 = set(event_text(e1).lower().split())
    t2 = set(event_text(e2).lower().split())
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def mmr_rerank(hits: list, *, lambda_: float = 0.7, top_n: int = None, sim=None) -> list:
    """Re-rank scored hits for relevance AND diversity (Maximal Marginal Relevance), so a
    cluster of near-duplicate events doesn't crowd out distinct ones (and waste a lean-context
    budget). `hits` = [{event, score}] with score = relevance (higher better). `lambda_`
    trades off: 1.0 = pure relevance, lower = more diversity. `sim(e1, e2) -> [0,1]` defaults
    to token-Jaccard on event text. Returns the hits re-ordered (same dicts)."""
    if not hits:
        return []
    sim = sim or _event_sim
    scores = [h.get("score", 0.0) for h in hits]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0
    rel = {id(h): (h.get("score", 0.0) - lo) / span for h in hits}  # normalize relevance to [0,1]

    selected: list = []
    remaining = list(hits)
    limit = top_n or len(hits)
    while remaining and len(selected) < limit:
        best, best_mmr = None, None
        for h in remaining:
            penalty = max((sim(h["event"], s["event"]) for s in selected), default=0.0)
            mmr = lambda_ * rel[id(h)] - (1.0 - lambda_) * penalty
            if best_mmr is None or mmr > best_mmr:
                best, best_mmr = h, mmr
        selected.append(best)
        remaining.remove(best)
    return selected


# ── memory-drift: reconcile recalled refs against the live workspace ──────

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reconcile_file_ref(file_path: str, remembered_sha: str, repo_root: str = None) -> dict:
    """Compare a recalled file reference against current workspace state.

    The ledger is the truth of what HAPPENED; the live file is the truth of what
    IS. Returns a drift verdict so the agent can trust current state over stale
    memory. drift=True means the recalled state no longer holds.
    """
    base = Path(repo_root or os.getcwd())
    target = Path(file_path)
    if not target.is_absolute():
        target = base / file_path

    if not target.exists():
        return {"file": file_path, "exists": False, "current_sha": None,
                "remembered_sha": remembered_sha, "drift": True,
                "reason": "file no longer exists (gone since it was recorded)"}

    try:
        current_sha = _sha256_bytes(target.read_bytes())
    except OSError as exc:
        return {"file": file_path, "exists": True, "current_sha": None,
                "remembered_sha": remembered_sha, "drift": True,
                "reason": f"could not read current file: {exc}"}

    if not remembered_sha:
        return {"file": file_path, "exists": True, "current_sha": current_sha,
                "remembered_sha": None, "drift": False,
                "reason": "no recorded baseline to compare (unverified)"}

    drift = current_sha != remembered_sha
    return {"file": file_path, "exists": True, "current_sha": current_sha,
            "remembered_sha": remembered_sha, "drift": drift,
            "reason": "content changed since it was recorded" if drift
                      else "matches recorded state"}


def _remembered_sha(event: dict) -> str:
    """Pull a recorded content sha from an event's result content-ref, if any."""
    result = event.get("result")
    if isinstance(result, dict):
        ref = result.get("_ref")
        if isinstance(ref, str) and ref.startswith("sha256:"):
            return ref.split(":", 1)[1]
    return ""


def annotate_drift(results: list, repo_root: str = None) -> list:
    """Annotate recall results that reference a file with a drift verdict.

    Results without a file reference get drift=None. Mutates+returns the list.
    """
    for hit in results:
        ev = hit.get("event", {})
        args = ev.get("args") or {}
        file_path = args.get("file_path") or args.get("filepath")
        if file_path:
            hit["drift"] = reconcile_file_ref(file_path, _remembered_sha(ev), repo_root)
        else:
            hit["drift"] = None
    return results


# ── tool handler (wired into the user-facing Recall tool) ─────────────────

def tool_recall(query: str, top_n: int = 5, mode: str = "auto",
                context: dict = None) -> dict:
    """Recall past ledger events matching a query, reconciled against live state."""
    repo_root = (context or {}).get("repo_root") or os.getcwd()
    events = load_events(_journal_path(repo_root))
    hits = search(events, query, top_n=int(top_n or 5), mode=mode)
    annotate_drift(hits, repo_root)
    return {
        "query": query,
        "count": len(hits),
        "results": [{
            "seq_id": h["event"].get("seq_id"),
            "tool_name": h["event"].get("tool_name"),
            "summary": event_text(h["event"])[:400],
            "score": h["score"],
            "drift": h.get("drift"),
        } for h in hits],
    }
