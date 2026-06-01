"""Tiered tool exposure + a lexical ToolSearch index.

Sending every tool's full JSON-Schema on every turn balloons input tokens and
degrades the model's attention as MCP/plugin tools accumulate into the hundreds.
Instead, tools carry an *exposure* tier:

  - "direct"   — always sent to the model (the core file/shell tools).
  - "deferred" — NOT sent; the model finds them via the ToolSearch tool, which
                 ranks deferred tools by a lexical score over name+description and
                 stages the matches so they're included on the NEXT turn.
  - "hidden"   — dispatch-only, never shown.

The ranker is a dependency-free BM25-flavoured TF scorer (korgex stays zero-dep).
Everything here is pure; the agent wires staging/serialization around it.
"""
from __future__ import annotations

import math
import re

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def rank(query: str, docs: list[dict], limit: int = 5) -> list[dict]:
    """Rank `docs` (each ``{"name", "description", ...}``) by lexical relevance to
    `query`, returning at most `limit`, best first. Docs that match no query term
    are excluded (a no-match query returns []). Scoring is TF over the combined
    name+description with an IDF-style rarity weight and a name-match bonus, so an
    exact tool-name hit and a rare descriptive term both rank well."""
    q_terms = set(_tokens(query))
    if not q_terms or not docs:
        return []

    # Document frequency for IDF — rarer query terms count for more.
    tokenized = []
    df: dict[str, int] = {}
    for d in docs:
        name_toks = _tokens(d.get("name", ""))
        body_toks = name_toks + _tokens(d.get("description", ""))
        tokenized.append((d, set(name_toks), body_toks))
        for t in set(body_toks):
            df[t] = df.get(t, 0) + 1

    n = len(docs)
    scored = []
    for d, name_set, body_toks in tokenized:
        score = 0.0
        for t in q_terms:
            tf = body_toks.count(t)
            if tf == 0:
                continue
            idf = math.log(1 + n / (1 + df.get(t, 0)))
            score += idf * (1 + math.log(tf))  # dampened term frequency
            if t in name_set:
                score += 2.0  # a query word that hits the tool NAME is a strong signal
        if score > 0:
            scored.append((score, d))

    scored.sort(key=lambda s: s[0], reverse=True)
    return [d for _score, d in scored[:limit]]


def assign_exposure(all_names, deferred_names, threshold: int = 100) -> dict:
    """Decide each tool's exposure tier.

    Below `threshold` total tools, keep everything ``direct`` — the surface is
    small enough that deferring only adds round-trips. At/above the threshold, any
    tool whose name is in `deferred_names` (e.g. MCP/plugin tools) flips to
    ``deferred``; core tools (never in that set) always stay ``direct``.
    """
    deferred_names = set(deferred_names or ())
    over = len(list(all_names)) >= threshold
    out = {}
    for name in all_names:
        out[name] = "deferred" if (over and name in deferred_names) else "direct"
    return out
