"""Verifiable security scanning — wrap the best available scanner, normalize its
findings, and let the ledger layer record them as tamper-evident, causally-linked
events.

Inspired by Trivy's model (one tool, many finding kinds: vulnerabilities, secrets,
misconfigurations, licenses) but korgex does not reimplement any of it — it *wraps*
whatever scanner is on the box (``trivy`` for breadth, else ``pip-audit`` / ``npm
audit`` / ``bandit`` per ecosystem) and turns the raw output into one common
``Finding`` shape. The differentiator is what korgex does next: a finding becomes a
provable ledger event tied to the exact code state, so ``korgex verify`` / ``trace``
/ ``why`` can answer "was this scanned, what did it find, and when" with a chain you
can check — not an ephemeral report.

This module is the pure heart: scanner output (already-parsed JSON) → normalized
findings + a summary. The IO (which scanner is installed, shelling out, recording to
the ledger) is layered on top so the normalization stays trivially testable.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass

from src.sanitize import redact

# Severity ranking so "worst finding" and sorting are well-defined across scanners
# that each spell severities their own way. Everything normalizes to these.
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}


@dataclass
class Finding:
    scanner: str          # which tool produced it: "trivy" | "pip-audit" | "bandit" | ...
    kind: str             # "vuln" | "secret" | "misconfig" | "license"
    severity: str         # normalized lowercase: critical|high|medium|low|unknown
    target: str           # file path, or "pkg@version" for a dependency
    id: str               # CVE / rule id
    title: str            # short human description
    fix: str = ""         # fixed version or remediation, when the scanner knows one


def _sev(raw) -> str:
    """Normalize a scanner's severity spelling to our lowercase scale."""
    s = str(raw or "").strip().lower()
    return s if s in SEVERITY_ORDER else "unknown"


def parse_trivy_json(data: dict) -> list:
    """Normalize ``trivy ... -f json`` output. Trivy groups findings under per-target
    Results, each carrying any of Vulnerabilities / Secrets / Misconfigurations."""
    findings: list = []
    for res in (data or {}).get("Results") or []:
        target = res.get("Target", "")
        for v in res.get("Vulnerabilities") or []:
            pkg = v.get("PkgName", "")
            ver = v.get("InstalledVersion", "")
            findings.append(Finding(
                scanner="trivy", kind="vuln", severity=_sev(v.get("Severity")),
                target=f"{pkg}@{ver}" if pkg else target,
                id=v.get("VulnerabilityID", ""), title=v.get("Title", ""),
                fix=v.get("FixedVersion", "")))
        for s in res.get("Secrets") or []:
            findings.append(Finding(
                scanner="trivy", kind="secret", severity=_sev(s.get("Severity")),
                target=target, id=s.get("RuleID", ""), title=s.get("Title", "")))
        for m in res.get("Misconfigurations") or []:
            findings.append(Finding(
                scanner="trivy", kind="misconfig", severity=_sev(m.get("Severity")),
                target=target, id=m.get("ID", ""), title=m.get("Title", ""),
                fix=m.get("Resolution", "")))
    return findings


def parse_pip_audit_json(data) -> list:
    """Normalize ``pip-audit -f json``: per-dependency vulns. pip-audit doesn't rate
    severity, so those land as ``unknown`` (still real, still recorded)."""
    findings: list = []
    for dep in (data or {}).get("dependencies") or []:
        target = f"{dep.get('name', '')}@{dep.get('version', '')}"
        for v in dep.get("vulns") or []:
            fixes = v.get("fix_versions") or []
            findings.append(Finding(
                scanner="pip-audit", kind="vuln", severity="unknown", target=target,
                id=v.get("id", ""), title=(v.get("description", "") or "")[:200],
                fix=fixes[0] if fixes else ""))
    return findings


def parse_bandit_json(data) -> list:
    """Normalize ``bandit -f json``: insecure-code weaknesses found by static analysis."""
    findings: list = []
    for r in (data or {}).get("results") or []:
        line = r.get("line_number")
        fn = r.get("filename", "")
        findings.append(Finding(
            scanner="bandit", kind="vuln", severity=_sev(r.get("issue_severity")),
            target=f"{fn}:{line}" if line else fn,
            id=r.get("test_id", ""), title=r.get("issue_text", "")))
    return findings


def summarize(findings) -> dict:
    """Roll findings up into counts (by severity, by scanner) and the worst severity
    present — the compact verdict that goes inline on the ledger event + the CLI."""
    by_severity: dict = {}
    by_scanner: dict = {}
    worst = None
    for f in findings or []:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        by_scanner[f.scanner] = by_scanner.get(f.scanner, 0) + 1
        if worst is None or SEVERITY_ORDER.get(f.severity, 0) > SEVERITY_ORDER.get(worst, 0):
            worst = f.severity
    return {
        "total": len(findings or []),
        "by_severity": by_severity,
        "by_scanner": by_scanner,
        "worst": worst,
    }


# ── which scanner to run ─────────────────────────────────────────────────────

# Python-project markers — pip-audit / bandit only make sense in a python tree.
_PY_MARKERS = ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg")


def detect_scanners(repo_root, *, which=shutil.which) -> list:
    """Scanners both installed AND applicable to ``repo_root``, in preference order:
    trivy first (broadest — vulns + secrets + misconfig + licenses, language-agnostic),
    then ecosystem tools gated on their project markers so we don't run a python
    auditor on a tree with no python."""
    out = []
    if which("trivy"):
        out.append("trivy")
    is_py = any(os.path.exists(os.path.join(repo_root, m)) for m in _PY_MARKERS)
    if is_py and which("pip-audit"):
        out.append("pip-audit")
    if is_py and which("bandit"):
        out.append("bandit")
    return out


_PARSERS = {
    "trivy": parse_trivy_json,
    "pip-audit": parse_pip_audit_json,
    "bandit": parse_bandit_json,
}


def _scan_cmd(scanner: str, repo_root: str) -> list:
    if scanner == "trivy":
        return ["trivy", "fs", "--quiet", "--format", "json",
                "--scanners", "vuln,secret,misconfig,license", repo_root]
    if scanner == "pip-audit":
        return ["pip-audit", "-f", "json", "--progress-spinner", "off"]
    if scanner == "bandit":
        return ["bandit", "-r", repo_root, "-f", "json", "-q"]
    return []


def _default_run(cmd, cwd=None):
    import subprocess
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    return p.returncode, p.stdout, p.stderr


def run_scan(repo_root, *, scanner=None, which=shutil.which, run=None) -> dict:
    """Run the best available scanner over ``repo_root`` → ``{scanner, findings,
    summary, ok, error}``. Security scanners exit nonzero WHEN they find issues, so a
    nonzero rc is not a failure here — only an absent scanner or unparseable output is.
    ``run(cmd, cwd) -> (rc, stdout, stderr)`` is injected so this tests offline."""
    run = run or _default_run
    scanners = [scanner] if scanner else detect_scanners(repo_root, which=which)
    if not scanners:
        return {"scanner": None, "findings": [], "summary": summarize([]), "ok": False,
                "error": "no supported scanner found (install trivy, pip-audit, or bandit)"}
    chosen = scanners[0]
    try:
        _rc, out, _err = run(_scan_cmd(chosen, repo_root), cwd=repo_root)
    except Exception as e:
        return {"scanner": chosen, "findings": [], "summary": summarize([]), "ok": False,
                "error": f"{chosen} failed to run: {e}"}
    try:
        data = json.loads(out) if (out or "").strip() else {}
    except (ValueError, TypeError):
        return {"scanner": chosen, "findings": [], "summary": summarize([]), "ok": False,
                "error": f"could not parse {chosen} output"}
    findings = _PARSERS[chosen](data)
    return {"scanner": chosen, "findings": findings, "summary": summarize(findings),
            "ok": True, "error": ""}


# ── verifiable recording ─────────────────────────────────────────────────────

def record_scan(client, result: dict, repo_root: str, *, triggered_by=None):
    """Record a scan as a tamper-evident, causally-linked ledger event
    (``security.scan``): itemized findings + the summary, redacted, best-effort.
    ``success`` mirrors the scan's ok flag. Returns the seq_id if assigned, else None."""
    if client is None:
        return None
    findings = result.get("findings") or []
    rows = [{"kind": f.kind, "severity": f.severity, "id": f.id,
             "target": f.target, "title": f.title} for f in findings]
    args = {"target": repo_root, "scanner": result.get("scanner")}
    res = {"summary": result.get("summary"), "ok": result.get("ok"),
           "findings": rows, "error": result.get("error", "")}
    try:
        return client.record_tool_call(
            tool_name="security.scan", args=redact(args), result=redact(res),
            success=bool(result.get("ok")), duration_ms=0, triggered_by=triggered_by)
    except Exception:
        return None
