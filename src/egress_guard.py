"""egress_guard.py — a shape-based guard over data leaving the box.

korgex can transmit data off-host through several tools (web, browser, bus, MCP,
remote-sign, network Bash). This inspects the OUTBOUND payload of those tools for
**secret shapes** (reusing `sanitize.py`'s patterns — one source of truth, no
drift) and **large encoded blobs** (possible exfil), and records each detection as
a tamper-evident verdict on the causal ledger.

Posture is set by ``KORGEX_EGRESS`` (default ``flag``, i.e. ON):
  off    — disabled (zero overhead)
  flag   — warn + ledger, never alters or blocks (default; additive, never breaks)
  redact — mask the secret in the outbound payload before it leaves
  block  — refuse the call

Leak-proofing invariant: a verdict goes onto a shareable, tamper-evident ledger,
so it must never itself carry the raw secret. Findings record only the secret's
SHAPE (kind/label/severity), never its value — see `verdict_payload`.

All pure; the thin wiring lives in `tool_abstraction.route_tool_call`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from urllib.parse import urlparse

from src import sanitize as _san


def split_env_list(var: str):
    """A comma-separated env var → a list of trimmed non-empty items, or None.
    Used for ``KORGEX_EGRESS_ALLOW`` / ``KORGEX_EGRESS_DENY`` host lists."""
    items = [x.strip() for x in os.environ.get(var, "").split(",") if x.strip()]
    return items or None

# Tools that transmit argument data off-box. (Bash is conditional — only when its
# command runs a network tool; MCP tool names are passed in at the wiring seam.)
OUTBOUND_TOOLS = frozenset({
    "WebFetch", "WebSearch", "BusSend", "RemoteSignTip", "browser_navigate",
})

# Per-tool "wire field": the single param whose value actually leaves the box.
# Tools absent here (MCP, RemoteSignTip) fall back to the whole params as JSON.
_FIELD = {
    "WebFetch": "url", "WebSearch": "query", "browser_navigate": "url",
    "Bash": "command", "BusSend": "message",
}

# Bash is the only HEURISTIC outbound surface — like the destructive-command guard,
# it's a best-effort floor against the obvious, not a sandbox. A determined exfil via
# `python3 -c`, `/dev/tcp` redirection, or a renamed binary evades it. The DECLARED
# outbound tools (web/search/bus/browser/MCP) are covered unconditionally; only Bash
# relies on spotting a network program in the command.
_NETWORK_CMDS = ("curl", "wget", "nc", "ncat", "netcat", "socat", "scp", "ssh",
                 "rsync", "ftp", "sftp", "telnet")
_NET_RE = re.compile(r"\b(" + "|".join(_NETWORK_CMDS) + r")\b")

# A long contiguous base64 run = possible encoded exfil (MEDIUM). 512 chars avoids
# normal tokens/hashes that would false-positive at a lower threshold.
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{512,}={0,2}")

# Presentation labels for a matched secret — derived from the matched text, so the
# DETECTION stays in sanitize._VALUE_RES (no second pattern set). Longest/most
# specific needles first.
_SHAPE_LABELS = (
    ("private key", "private_key"), ("sk-ant", "anthropic_key"), ("sk-or", "openrouter_key"),
    ("sk-", "openai_key"), ("github_pat", "github_pat"), ("ghp_", "github_token"),
    ("gho_", "github_token"), ("ghu_", "github_token"), ("ghs_", "github_token"),
    ("ghr_", "github_token"), ("pypi-", "pypi_token"), ("akia", "aws_key"),
    ("aiza", "google_key"), ("xox", "slack_token"), ("eyj", "jwt"), ("bearer", "bearer_token"),
)

_MODES = ("off", "flag", "redact", "block")


def mode_from_env(env: "dict | None") -> str:
    """``KORGEX_EGRESS`` → one of off/flag/redact/block. Default ``flag`` (ON);
    anything unrecognized falls back to ``flag`` (never crashes, never silently off)."""
    raw = str((env or {}).get("KORGEX_EGRESS", "") or "").strip().lower()
    return raw if raw in _MODES else "flag"


def is_outbound(tool_name: str, params: dict, mcp_tools=None) -> bool:
    """True iff this call transmits data off-box: a known outbound tool, an MCP
    tool (server-routed), or a Bash command that runs a network program."""
    if tool_name in OUTBOUND_TOOLS:
        return True
    if mcp_tools and tool_name in mcp_tools:
        return True
    if tool_name == "Bash":
        return bool(_NET_RE.search(str((params or {}).get("command", "") or "")))
    return False


def outbound_text(tool_name: str, params: dict) -> str:
    """The payload that would leave the box for this tool — its wire field, or the
    whole params as JSON for tools without a single field (MCP, RemoteSignTip).
    Tolerant: a missing field yields ``""``."""
    params = params or {}
    field = _FIELD.get(tool_name)
    if field is not None:
        return str(params.get(field) or "")
    try:
        return json.dumps(params, default=str, sort_keys=True)
    except Exception:
        return str(params)


def extract_destination(tool_name: str, params: dict) -> "str | None":
    """Best-effort host/identity the data is bound for: a URL host, the bus
    recipient (``BusSend.to``), an MCP server name (``server__tool``), or a host
    parsed out of a network Bash command. ``None`` when it can't be determined —
    in which case the allow/deny host lists don't apply to this call."""
    params = params or {}
    url = params.get("url")
    if url:
        try:
            host = urlparse(str(url)).hostname
            if host:
                return host
        except Exception:
            pass
    if tool_name == "BusSend" and params.get("to"):
        return str(params.get("to"))           # the recipient agent IS the destination
    if "__" in tool_name:
        return tool_name.split("__", 1)[0]
    if tool_name == "Bash":
        cmd = str(params.get("command", "") or "")
        m = re.search(r"https?://([^/\s:]+)", cmd) or re.search(r"@([\w.-]+):", cmd)
        if m:
            return m.group(1)
    return None


def _label_for(matched: str) -> str:
    low = matched.lower()
    for needle, label in _SHAPE_LABELS:
        if needle in low:
            return label
    return "secret"


def scan_payload(text: "str | None") -> "list[dict]":
    """Findings for one payload: secret shapes (HIGH, reusing sanitize's regexes) +
    a large base64 blob (MEDIUM). Each Finding is ``{kind, label, severity}`` and
    NEVER carries the matched value (leak-proofing). Note: DETECTION lives entirely
    in ``sanitize._VALUE_RES`` (one source of truth); ``_SHAPE_LABELS`` only adds a
    human label and falls back to ``"secret"`` for an unlabeled shape — so a new
    sanitize pattern is still detected, just labeled generically."""
    if not isinstance(text, str) or not text:
        return []
    findings = []
    seen = set()
    for pat in _san._VALUE_RES:
        m = pat.search(text)
        if m:
            label = _label_for(m.group(0))
            if label not in seen:
                seen.add(label)
                findings.append({"kind": "secret", "label": label, "severity": "high"})
    if _BASE64_RE.search(text):
        findings.append({"kind": "blob", "label": "base64_blob", "severity": "medium"})
    return findings


def _host_match(dest: str, entry: str) -> bool:
    dest, entry = dest.lower(), entry.lower()
    return dest == entry or dest.endswith("." + entry)


def inspect(tool_name: str, params: dict, *, allow=None, deny=None, mcp_tools=None) -> dict:
    """Compose a verdict: findings + rolled-up severity + destination + whether the
    destination is denied by the operator's allow/deny host lists."""
    findings = scan_payload(outbound_text(tool_name, params))
    dest = extract_destination(tool_name, params)
    if any(f["severity"] == "high" for f in findings):
        severity = "high"
    elif findings:
        severity = "medium"
    else:
        severity = None
    denied = False
    if dest:
        if deny and any(_host_match(dest, d) for d in deny):
            denied = True
        elif allow and not any(_host_match(dest, a) for a in allow):
            denied = True
    return {"findings": findings, "severity": severity, "destination": dest,
            "denied_by_list": denied}


def _redact_params(tool_name: str, params: dict) -> dict:
    """A COPY of params with secrets masked in the outbound field (or, for
    field-less tools, the whole structure via sanitize)."""
    field = _FIELD.get(tool_name)
    if field is not None and field in (params or {}):
        new = dict(params)
        new[field] = _san._redact_string(str(new.get(field) or ""))
        return new
    return _san.redact(params)


def apply(verdict: dict, tool_name: str, params: dict, mode: str) -> "tuple[dict, str]":
    """Act per mode. Returns ``(params, action)`` where action ∈
    ``allow|redacted|blocked``. flag never alters; redact masks the outbound
    secret; block refuses. A destination on the operator's DENY list (or outside
    the ALLOW list) is a host-level refusal that masking can't resolve, so it
    blocks in both ``redact`` and ``block`` modes (``flag`` stays advisory)."""
    if mode == "block":
        return params, "blocked"
    if mode == "redact":
        if verdict.get("denied_by_list"):
            return params, "blocked"
        return _redact_params(tool_name, params), "redacted"
    return params, "allow"


def _policy_hash(mode: str, allow=None, deny=None) -> str:
    h = hashlib.sha256()
    h.update(mode.encode())
    h.update(json.dumps({"allow": sorted(allow or []), "deny": sorted(deny or [])},
                        sort_keys=True).encode())
    return h.hexdigest()[:16]


def verdict_payload(tool_name: str, verdict: dict, *, mode: str, action: str,
                    allow=None, deny=None) -> dict:
    """The ledger event body. Carries finding SHAPES (kind/label/severity) and the
    destination host — NEVER the raw secret, so the shareable ledger can't itself
    become the exfil channel."""
    return {
        "tool": tool_name,
        "destination": verdict.get("destination"),
        "findings": verdict.get("findings", []),
        "severity": verdict.get("severity"),
        "denied_by_list": verdict.get("denied_by_list", False),
        "mode": mode,
        "action": action,
        "policy_hash": _policy_hash(mode, allow, deny),
    }
