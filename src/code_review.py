"""korgex review — verifiable code review.

A diff is reviewed across dimensions (correctness / security / performance /
maintainability), each finding is then ADVERSARIALLY VERIFIED (a second pass that
must confirm it's a real issue, not a plausible-but-wrong nit), and confirmed
findings are recorded as tamper-evident `review.finding` ledger events — so
``korgex verify`` / ``trace`` / ``why`` can prove a review happened and what it found,
not just hand you an ephemeral comment.

The LLM is injected (``reviewer`` / ``verifier`` callables, mirroring skill_review),
so the whole core tests offline. The CLI wires the agent's real model in.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from src.sanitize import redact

SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
DIMENSIONS = ["correctness", "security", "performance", "maintainability"]


@dataclass
class Finding:
    dimension: str
    severity: str
    file: str
    title: str
    line: int | None = None
    detail: str = ""
    suggestion: str = ""
    confirmed: bool | None = None   # set by the adversarial verify pass


def _sev(raw) -> str:
    s = str(raw or "").strip().lower()
    return s if s in SEVERITY_ORDER else "medium"


# ── diff acquisition ──────────────────────────────────────────────────────────

def _default_run(cmd):
    import subprocess
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


def get_diff(base: str = "main", *, run=None) -> str:
    """The unified diff to review. ``base='staged'`` → staged changes; ``'working'`` →
    unstaged; otherwise ``git diff <base>...HEAD`` (this branch's changes vs base).
    ``run(cmd) -> (rc, stdout, stderr)`` is injected so this tests offline."""
    run = run or _default_run
    if base == "staged":
        cmd = ["git", "diff", "--cached"]
    elif base == "working":
        cmd = ["git", "diff"]
    else:
        cmd = ["git", "diff", f"{base}...HEAD"]
    try:
        _rc, out, _err = run(cmd)
    except Exception:
        return ""
    return out or ""


# ── review (LLM-driven, injected) ───────────────────────────────────────────

_REVIEW_SYSTEM = (
    "You are a senior code reviewer. Review the unified diff for real, actionable "
    "issues across four dimensions: correctness (bugs, logic errors, missed edge "
    "cases), security (injection, secrets, auth, unsafe input), performance (N+1, "
    "needless work, blocking calls), maintainability (unclear code, dead code, missing "
    "error handling). Report ONLY genuine issues in the CHANGED lines — no style nits, "
    "no praise, no speculation. Reply with ONLY a JSON array; each element is "
    '{"dimension":"correctness|security|performance|maintainability",'
    '"severity":"critical|high|medium|low","file":"path","line":<int or null>,'
    '"title":"a specific one-line summary of THE ISSUE (not the category word)",'
    '"detail":"why it matters","suggestion":"the fix"}. '
    'Use exactly those key names and no others, and set "file" to the path from the diff\'s '
    '"+++ b/" header. Example element: {"dimension":"security","severity":"high",'
    '"file":"src/auth.py","line":42,"title":"User input concatenated into the SQL query",'
    '"detail":"enables SQL injection","suggestion":"use a parameterized query"}. '
    "Return [] if the diff is clean."
)


def build_review_prompt(diff: str) -> str:
    return ("Review this diff and return the JSON array of findings only:\n\n"
            f"```diff\n{diff}\n```")


_DIM_MAP = {
    "security": "security", "vulnerability": "security", "vuln": "security",
    "correctness": "correctness", "bug": "correctness", "logic": "correctness",
    "reliability": "correctness", "performance": "performance", "perf": "performance",
    "maintainability": "maintainability", "maintenance": "maintainability", "style": "maintainability",
}


def _first(d: dict, *keys) -> str:
    """First non-empty string among `keys` — models name their fields differently."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


_DIM_KEYWORDS = [
    ("security", ("inject", "secret", "token", "password", "credential", "auth", "xss",
                  "sql", "sanitiz", "unsafe", "vuln", "shell", "eval(", "ssrf", "csrf", "sensitive")),
    ("performance", ("n+1", "n + 1", "perf", "slow", "latency", "blocking", "o(n",
                     "inefficient", "memory leak", "redundant quer")),
    ("maintainability", ("readab", "dead code", "duplicat", "unclear", "naming",
                         "magic number", "overly complex")),
]


def _infer_dimension(text: str) -> str:
    """Best-effort dimension from a finding's text — for when the model omits it (so a
    security issue isn't mislabeled 'correctness')."""
    t = (text or "").lower()
    for dim, kws in _DIM_KEYWORDS:
        if any(k in t for k in kws):
            return dim
    return "correctness"


def parse_review_reply(text: str) -> list:
    """Pull the first JSON array out of a model reply (tolerating prose/fences) and
    build Findings. TOLERANT of how different models name fields — e.g. gpt-4o returns
    `type`/`message` and often omits `file`. Only a missing TITLE drops a finding;
    `file` is optional (filled later from a single-file diff). Never raises."""
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        title = _first(d, "title", "message", "msg", "issue", "summary", "description")
        if not title:
            continue
        detail = _first(d, "detail", "explanation", "description", "message", "rationale")
        if detail == title:
            detail = ""                       # the model reused one field for both
        suggestion = _first(d, "suggestion", "fix", "recommendation", "remediation")
        dim_raw = _first(d, "dimension", "type", "category", "kind")
        dim = (_DIM_MAP.get(dim_raw.lower(), dim_raw.lower()) if dim_raw
               else _infer_dimension(f"{title} {detail} {suggestion}"))
        line = d.get("line") if d.get("line") is not None else d.get("line_number")
        try:
            line = int(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        out.append(Finding(
            dimension=dim, severity=_sev(d.get("severity")),
            file=_first(d, "file", "path", "location", "filename"),
            title=title, line=line, detail=detail, suggestion=suggestion))
    return out


def make_reviewer(complete):
    """Build a ``reviewer(diff) -> [Finding]`` from a ``complete(system, user) -> str``
    callable (the agent's one-off LLM call). Decoupled so it tests offline."""
    def reviewer(diff):
        return parse_review_reply(complete(_REVIEW_SYSTEM, build_review_prompt(diff)))
    return reviewer


def _diff_files(diff: str) -> list:
    """Files touched by a unified diff, from its `+++ b/<path>` headers."""
    return [m.group(1).strip() for m in re.finditer(r"^\+\+\+ b/(.+)$", diff or "", re.MULTILINE)]


def review_diff(diff: str, reviewer) -> list:
    """Review a diff via the injected reviewer. An empty diff makes no model call and
    returns []. When a finding came back without a file and the diff touches exactly one
    file, fill it in (models routinely omit `file` on a single-file diff). Best-effort:
    a reviewer error yields []."""
    if not diff or not diff.strip():
        return []
    try:
        findings = reviewer(diff) or []
    except Exception:
        return []
    files = _diff_files(diff)
    if len(files) == 1:
        findings = [replace(f, file=f.file or files[0]) for f in findings]
    return findings


# ── adversarial verify ──────────────────────────────────────────────────────

_VERIFY_SYSTEM = (
    "You are verifying a code-review finding against the diff. Decide whether it is a "
    "REAL, correct issue in the changed code — not a false positive, not style. Reply "
    'with ONLY a JSON object: {"confirmed": true|false, "reason":"one line"}.'
)


def build_verify_prompt(finding, diff: str) -> str:
    loc = f":{finding.line}" if finding.line else ""
    return (f"Finding [{finding.dimension}/{finding.severity}] in {finding.file}{loc} — "
            f"{finding.title}\n{finding.detail}\n\nDiff:\n```diff\n{diff}\n```\n\n"
            "Is this a real issue?")


def parse_verdict(text: str) -> bool:
    """True = keep the finding. Only an explicit ``confirmed: false`` drops it; anything
    unclear keeps it — don't lose a real bug to a parse glitch."""
    if not text:
        return True
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0))
            if isinstance(v, dict) and "confirmed" in v:
                return bool(v["confirmed"])
        except (ValueError, TypeError):
            pass
    return True


def make_verifier(complete, diff: str):
    """Build a ``verifier(finding) -> bool`` from ``complete`` + the diff."""
    def verifier(finding):
        return parse_verdict(complete(_VERIFY_SYSTEM, build_verify_prompt(finding, diff)))
    return verifier


def verify_findings(findings, verifier) -> list:
    """Run each finding through the adversarial verifier, returning copies with
    ``confirmed`` set. A verifier error defaults to confirmed (keep)."""
    out = []
    for f in findings or []:
        try:
            ok = bool(verifier(f))
        except Exception:
            ok = True
        out.append(replace(f, confirmed=ok))
    return out


# ── summary + ledger ──────────────────────────────────────────────────────────

def summarize(findings) -> dict:
    by_severity, by_dimension, worst, confirmed = {}, {}, None, 0
    for f in findings or []:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        by_dimension[f.dimension] = by_dimension.get(f.dimension, 0) + 1
        if f.confirmed:
            confirmed += 1
        if worst is None or SEVERITY_ORDER.get(f.severity, 0) > SEVERITY_ORDER.get(worst, 0):
            worst = f.severity
    return {"total": len(findings or []), "by_severity": by_severity,
            "by_dimension": by_dimension, "worst": worst, "confirmed": confirmed}


def record_review(client, findings, base: str, *, triggered_by=None) -> int:
    """Record each finding as a tamper-evident ``review.finding`` ledger event — a
    high/critical finding lands with ``success=False`` so it stands out in the trace.
    Returns the count recorded. Never raises."""
    if client is None or not findings:
        return 0
    n = 0
    for f in findings:
        args = redact({"file": f.file, "line": f.line, "dimension": f.dimension,
                       "severity": f.severity, "base": base})
        result = redact({"title": f.title, "detail": f.detail,
                         "suggestion": f.suggestion, "confirmed": f.confirmed})
        try:
            client.record_tool_call(
                tool_name="review.finding", args=args, result=result,
                success=SEVERITY_ORDER.get(f.severity, 0) < 3,   # high/critical → False (flagged)
                duration_ms=0, triggered_by=triggered_by)
            n += 1
        except Exception:
            pass
    return n
