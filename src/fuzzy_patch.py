"""Forgiving SEARCH/REPLACE application.

An exact-string `Edit` fails the moment the model's `old_string` differs from the
file by trivia — a few spaces of indent, trailing whitespace, tabs-vs-spaces. That
makes large multi-file edits brittle. This matches in two safe tiers:

1. **exact** — the substring is present verbatim; replace the first occurrence.
2. **whitespace-tolerant** — find the contiguous block of lines whose content
   matches ignoring per-line leading/trailing whitespace, and replace that block.

It deliberately stops there. It does NOT do similarity/closest-match guessing —
that risks editing the wrong code, which is worse than a clean failure. If neither
tier matches, it reports ``not-found`` and changes nothing, so the caller can
re-Read and retry.
"""
from __future__ import annotations


def find_and_replace(content: str, search: str, replace: str):
    """Return ``(new_content, status, detail)``.

    status ∈ exact | fuzzy-whitespace | not-found | empty-search.
    On not-found/empty-search, ``new_content`` is the original (no change).
    """
    if not search:
        return content, "empty-search", None

    # 1. exact substring (first occurrence)
    if search in content:
        return content.replace(search, replace, 1), "exact", None

    # 2. whitespace-tolerant contiguous line-block match
    c_lines = content.split("\n")
    s_lines = search.split("\n")
    region = _find_line_block(c_lines, s_lines)
    if region is not None:
        start, end = region
        new_lines = c_lines[:start] + replace.split("\n") + c_lines[end:]
        return "\n".join(new_lines), "fuzzy-whitespace", f"matched lines {start + 1}-{end}"

    return content, "not-found", None


def _norm(line: str) -> str:
    """A line's content ignoring leading/trailing whitespace (so indent + trailing
    spaces don't block a match). Tabs/spaces in the interior are left as-is."""
    return line.strip()


def _find_line_block(c_lines: list, s_lines: list):
    """Find ``(start, end)`` such that ``c_lines[start:end]`` equals ``s_lines``
    line-for-line after whitespace-normalizing each line. None if no unique-enough
    contiguous run matches. Returns the FIRST such run."""
    # Drop blank lines only at the very ends of the search (common when a model
    # includes a leading/trailing newline); interior blanks must still align.
    s_norm = [_norm(x) for x in s_lines]
    while s_norm and s_norm[0] == "":
        s_norm.pop(0)
        s_lines = s_lines[1:]
    while s_norm and s_norm[-1] == "":
        s_norm.pop()
        s_lines = s_lines[:-1]
    n = len(s_norm)
    if n == 0:
        return None
    for start in range(len(c_lines) - n + 1):
        if [_norm(x) for x in c_lines[start:start + n]] == s_norm:
            return (start, start + n)
    return None
