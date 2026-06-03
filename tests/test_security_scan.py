"""Verifiable security scanning — wrap the best available scanner (trivy, else
pip-audit / npm audit / bandit), normalize its output to a common Finding shape, and
(in the ledger layer) record each as a tamper-evident event.

These pin the pure heart — scanner JSON → normalized findings + a summary — so the
integration/IO layer around it stays a thin, mockable shell.
"""
from __future__ import annotations

from src import security_scan as SS

TRIVY_FS = {
    "SchemaVersion": 2,
    "Results": [
        {
            "Target": "requirements.txt",
            "Class": "lang-pkgs",
            "Type": "pip",
            "Vulnerabilities": [
                {"VulnerabilityID": "CVE-2023-1234", "PkgName": "requests",
                 "InstalledVersion": "2.0.0", "FixedVersion": "2.31.0",
                 "Severity": "HIGH", "Title": "requests SSRF"},
            ],
        },
        {
            "Target": "app/config.yaml",
            "Class": "secret",
            "Secrets": [
                {"RuleID": "aws-access-key-id", "Severity": "CRITICAL",
                 "Title": "AWS Access Key", "StartLine": 3},
            ],
        },
        {
            "Target": "Dockerfile",
            "Class": "config",
            "Type": "dockerfile",
            "Misconfigurations": [
                {"ID": "DS002", "Severity": "MEDIUM", "Title": "Run as root",
                 "Resolution": "Add a USER instruction"},
            ],
        },
    ],
}


def test_parse_trivy_json_normalizes_vulns_secrets_misconfig():
    findings = SS.parse_trivy_json(TRIVY_FS)
    by_kind = {f.kind: f for f in findings}
    assert set(by_kind) == {"vuln", "secret", "misconfig"}

    v = by_kind["vuln"]
    assert v.id == "CVE-2023-1234"
    assert v.severity == "high"                       # normalized to lowercase
    assert "requests" in v.target and "2.0.0" in v.target
    assert v.fix == "2.31.0"
    assert v.scanner == "trivy"

    s = by_kind["secret"]
    assert s.severity == "critical"
    assert s.target == "app/config.yaml"
    assert s.id == "aws-access-key-id"

    m = by_kind["misconfig"]
    assert m.severity == "medium"
    assert m.target == "Dockerfile"
    assert m.id == "DS002"


def test_parse_trivy_json_empty_is_no_findings():
    assert SS.parse_trivy_json({}) == []
    assert SS.parse_trivy_json({"Results": []}) == []
    assert SS.parse_trivy_json({"Results": [{"Target": "x"}]}) == []   # no finding arrays


def test_summarize_counts_by_severity_scanner_and_worst():
    summary = SS.summarize(SS.parse_trivy_json(TRIVY_FS))
    assert summary["total"] == 3
    assert summary["by_severity"]["critical"] == 1
    assert summary["by_severity"]["high"] == 1
    assert summary["by_severity"]["medium"] == 1
    assert summary["worst"] == "critical"             # highest-severity present
    assert summary["by_scanner"]["trivy"] == 3


def test_summarize_empty_has_no_worst():
    s = SS.summarize([])
    assert s["total"] == 0
    assert s["worst"] is None


PIP_AUDIT = {
    "dependencies": [
        {"name": "requests", "version": "2.0.0",
         "vulns": [{"id": "PYSEC-2023-1", "fix_versions": ["2.31.0"],
                    "description": "SSRF in requests"}]},
        {"name": "safe-pkg", "version": "1.0", "vulns": []},
    ]
}


def test_parse_pip_audit_json():
    findings = SS.parse_pip_audit_json(PIP_AUDIT)
    assert len(findings) == 1                         # only the vulnerable dep
    f = findings[0]
    assert f.scanner == "pip-audit"
    assert f.kind == "vuln"
    assert f.id == "PYSEC-2023-1"
    assert f.target == "requests@2.0.0"
    assert f.fix == "2.31.0"
    assert f.severity == "unknown"                    # pip-audit doesn't rate severity


BANDIT = {
    "results": [
        {"filename": "app.py", "issue_severity": "HIGH", "test_id": "B602",
         "issue_text": "subprocess call with shell=True", "line_number": 10},
    ]
}


def test_parse_bandit_json():
    findings = SS.parse_bandit_json(BANDIT)
    assert len(findings) == 1
    f = findings[0]
    assert f.scanner == "bandit"
    assert f.kind == "vuln"                            # insecure-code weakness
    assert f.severity == "high"
    assert f.id == "B602"
    assert "app.py" in f.target


def test_parsers_tolerate_garbage():
    assert SS.parse_pip_audit_json({}) == []
    assert SS.parse_pip_audit_json(None) == []
    assert SS.parse_bandit_json({}) == []
    assert SS.parse_bandit_json(None) == []


# ── detection + running (IO injected) ────────────────────────────────────────

def test_detect_scanners_prefers_trivy_and_respects_ecosystem(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert SS.detect_scanners(
        str(tmp_path), which=lambda n: n if n == "pip-audit" else None) == ["pip-audit"]
    assert SS.detect_scanners(
        str(tmp_path), which=lambda n: n if n in ("trivy", "pip-audit") else None)[0] == "trivy"


def test_detect_scanners_skips_ecosystem_tools_without_their_markers(tmp_path):
    # pip-audit installed but no python project → not selected
    assert SS.detect_scanners(str(tmp_path), which=lambda n: n) != []  # trivy (no marker needed)
    assert SS.detect_scanners(
        str(tmp_path), which=lambda n: n if n == "pip-audit" else None) == []  # no py markers


def test_detect_scanners_none_when_nothing_installed(tmp_path):
    assert SS.detect_scanners(str(tmp_path), which=lambda n: None) == []


def test_run_scan_uses_injected_runner_and_parses(tmp_path):
    import json as _j
    calls = {}

    def fake_run(cmd, cwd=None):
        calls["cmd"], calls["cwd"] = cmd, cwd
        return (1, _j.dumps(TRIVY_FS), "")          # trivy exits nonzero WHEN it finds things

    result = SS.run_scan(str(tmp_path), scanner="trivy", run=fake_run)
    assert result["ok"] is True
    assert result["scanner"] == "trivy"
    assert result["summary"]["worst"] == "critical"
    assert calls["cmd"][0] == "trivy" and calls["cwd"] == str(tmp_path)


def test_run_scan_no_scanner_available(tmp_path):
    result = SS.run_scan(str(tmp_path), which=lambda n: None)
    assert result["ok"] is False
    assert "no supported scanner" in result["error"]


def test_run_scan_unparseable_output_is_an_error_not_a_crash(tmp_path):
    result = SS.run_scan(str(tmp_path), scanner="trivy",
                         run=lambda cmd, cwd=None: (2, "not json at all", "boom"))
    assert result["ok"] is False
    assert "parse" in result["error"]


# ── ledger recording (the verifiable bit) ────────────────────────────────────

class _CaptureClient:
    def __init__(self):
        self.calls = []

    def record_tool_call(self, **k):
        self.calls.append(k)
        return 7


def test_record_scan_emits_a_verifiable_security_event():
    import json as _j
    result = SS.run_scan(".", scanner="trivy",
                         run=lambda cmd, cwd=None: (0, _j.dumps(TRIVY_FS), ""))
    c = _CaptureClient()
    seq = SS.record_scan(c, result, "/repo", triggered_by=3)
    assert seq == 7
    call = c.calls[0]
    assert call["tool_name"] == "security.scan"
    assert call["args"]["scanner"] == "trivy"
    assert call["result"]["summary"]["worst"] == "critical"
    assert call["triggered_by"] == 3
    kinds = {f["kind"] for f in call["result"]["findings"]}   # itemized for audit
    assert {"vuln", "secret", "misconfig"} <= kinds


def test_record_scan_handles_none_client():
    assert SS.record_scan(None, {"findings": [], "summary": {}, "ok": True}, "/r") is None


# ── `korgex scan` CLI ─────────────────────────────────────────────────────────

def test_cmd_scan_reports_findings_and_gates_on_high(monkeypatch, capsys):
    from src import cli
    from src import security_scan as SS2
    fake = {"scanner": "trivy", "ok": True, "error": "",
            "findings": [SS2.Finding("trivy", "vuln", "high", "requests@2.0.0",
                                      "CVE-1", "SSRF", "2.31.0")],
            "summary": SS2.summarize([SS2.Finding("trivy", "vuln", "high",
                                                   "requests@2.0.0", "CVE-1", "SSRF")])}
    monkeypatch.setattr(SS2, "run_scan", lambda *a, **k: fake)
    monkeypatch.setattr("src.korg_ledger.get_default_client", lambda: None)
    monkeypatch.setattr("sys.argv", ["korgex", "scan", "."])
    rc = cli.cmd_scan()
    out = capsys.readouterr().out
    assert "CVE-1" in out and "trivy" in out
    assert rc == 1                                 # a high finding → nonzero (CI gate)


def test_cmd_scan_clean_exits_zero(monkeypatch, capsys):
    from src import cli
    from src import security_scan as SS2
    monkeypatch.setattr(SS2, "run_scan", lambda *a, **k: {
        "scanner": "trivy", "ok": True, "error": "", "findings": [],
        "summary": SS2.summarize([])})
    monkeypatch.setattr("src.korg_ledger.get_default_client", lambda: None)
    monkeypatch.setattr("sys.argv", ["korgex", "scan"])
    rc = cli.cmd_scan()
    assert rc == 0
    assert "no security findings" in capsys.readouterr().out


def test_cmd_scan_no_scanner_returns_2(monkeypatch, capsys):
    from src import cli
    from src import security_scan as SS2
    monkeypatch.setattr(SS2, "run_scan", lambda *a, **k: {
        "scanner": None, "ok": False, "error": "no supported scanner found",
        "findings": [], "summary": SS2.summarize([])})
    monkeypatch.setattr("sys.argv", ["korgex", "scan"])
    rc = cli.cmd_scan()
    assert rc == 2
    assert "no supported scanner" in capsys.readouterr().out


# ── agent tool (the agent can scan; the loop ledgers it causally) ─────────────

def test_security_scan_tool_is_registered():
    from src import tool_abstraction as TA
    names = {s["name"] for s in TA.get_user_tool_schemas()}
    assert "security_scan" in names


def test_security_scan_tool_routes_and_returns_serializable_findings(monkeypatch):
    from src import security_scan as SS2
    from src import tool_abstraction as TA
    fake = {"scanner": "trivy", "ok": True, "error": "",
            "findings": [SS2.Finding("trivy", "vuln", "high", "requests@2.0.0",
                                     "CVE-1", "SSRF", "2.31.0")],
            "summary": SS2.summarize([SS2.Finding("trivy", "vuln", "high",
                                                  "requests@2.0.0", "CVE-1", "SSRF")])}
    monkeypatch.setattr(SS2, "run_scan", lambda *a, **k: fake)
    res = TA.route_tool_call("security_scan", {"path": "/repo"}, repo_root="/repo")
    assert res["ok"] is True
    assert res["scanner"] == "trivy"
    assert res["summary"]["worst"] == "high"
    f0 = res["findings"][0]                       # serializable dict, not a Finding
    assert isinstance(f0, dict)
    assert f0["id"] == "CVE-1" and f0["fix"] == "2.31.0"
