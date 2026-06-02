"""
Korgex bridge tests — Day-1 acceptance gate.

These verify:
1. The user-facing tools (Write/Read/Edit) actually route to the internal
   handlers in tools_impl and produce filesystem effects.
2. Unknown tools fail gracefully with a structured error instead of raising.
3. Provider tool schemas have the right shape for Anthropic and OpenAI.
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Allow running from repo root without an install
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.tool_abstraction import route_tool_call  # noqa: E402
from src.agent import KorgexAgent  # noqa: E402


# ── 1. Router smoke tests ─────────────────────────────────────────────────


def test_write_routes_to_disk(tmp_path):
    target = tmp_path / "hello.txt"
    r = route_tool_call("Write", {"file_path": str(target), "content": "hi"})
    assert "error" not in r, f"Write failed: {r}"
    assert target.read_text() == "hi"


def test_read_routes(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("print('a')")
    r = route_tool_call("Read", {"file_path": str(f)})
    assert "error" not in r, f"Read failed: {r}"
    assert "print('a')" in r.get("content", "")


def test_read_ignores_unsupported_kwargs(tmp_path):
    # Schema has offset/limit but the handler doesn't — should be filtered, not crash
    f = tmp_path / "x.py"
    f.write_text("line1\nline2\n")
    r = route_tool_call("Read", {"file_path": str(f), "offset": 0, "limit": 100})
    assert "error" not in r, f"Read with extra kwargs failed: {r}"


def test_edit_adapter_constructs_search_replace(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("foo = 1\nbar = 2\n")
    r = route_tool_call("Edit", {
        "file_path": str(f),
        "old_string": "foo = 1",
        "new_string": "foo = 999",
    })
    assert "error" not in r, f"Edit failed: {r}"
    assert "foo = 999" in f.read_text()
    assert "foo = 1\n" not in f.read_text()


def test_unknown_tool_returns_error_not_raise():
    r = route_tool_call("NotARealTool", {})
    assert isinstance(r, dict)
    assert "error" in r
    assert "Unknown tool" in r["error"]


# ── 2. Provider tool-schema shape ─────────────────────────────────────────


def test_provider_tool_format_anthropic():
    a = KorgexAgent(model="claude-sonnet-4-6")
    assert a.provider == "anthropic"
    tools = a._get_provider_tools()
    assert len(tools) > 0
    for t in tools:
        assert set(t.keys()) == {"name", "description", "input_schema"}, \
            f"Anthropic tool has wrong shape: {t.keys()}"
        assert "function" not in t


def test_provider_tool_format_openai():
    a = KorgexAgent(model="gpt-4o")
    assert a.provider == "openai"
    tools = a._get_provider_tools()
    assert len(tools) > 0
    for t in tools:
        assert t.get("type") == "function"
        assert "function" in t
        assert set(t["function"].keys()) == {"name", "description", "parameters"}


def test_openrouter_anthropic_id_detected():
    # OpenRouter routes Anthropic models with "anthropic/claude-..." IDs
    a = KorgexAgent(model="anthropic/claude-sonnet-4-6")
    assert a.provider == "anthropic"


def test_missing_api_key_raises_cleanly(tmp_path):
    saved = {k: os.environ.pop(k, None) for k in
             ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "KORGEX_API_KEY")}
    # Point config at an empty file too, so a real ~/.korgex/config.json with a
    # saved key doesn't satisfy the lookup (the agent now reads config + env).
    saved_cfg = os.environ.pop("KORGEX_CONFIG", None)
    os.environ["KORGEX_CONFIG"] = str(tmp_path / "empty.json")
    try:
        a = KorgexAgent(model="claude-sonnet-4-6")
        with pytest.raises(RuntimeError, match="API key"):
            a._get_client()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        if saved_cfg is not None:
            os.environ["KORGEX_CONFIG"] = saved_cfg
        else:
            os.environ.pop("KORGEX_CONFIG", None)


# ── 3. Mode → model resolution ───────────────────────────────────────────


def test_mode_plan_picks_opus():
    a = KorgexAgent(mode="plan")
    assert "opus" in a.model.lower()
    assert a.provider == "anthropic"


def test_mode_execute_picks_sonnet():
    a = KorgexAgent(mode="execute")
    assert "sonnet" in a.model.lower()


def test_mode_debug_picks_haiku():
    a = KorgexAgent(mode="debug")
    assert "haiku" in a.model.lower()


def test_explicit_model_overrides_mode():
    a = KorgexAgent(model="gpt-4o", mode="plan")
    assert a.model == "gpt-4o"
    assert a.provider == "openai"


def test_default_model_when_nothing_specified(monkeypatch):
    # Hermetic: no explicit model, no env, AND no config default → the builtin.
    # (Must mock load_config — otherwise this reads the dev's real ~/.korgex config,
    # which now legitimately overrides the builtin.)
    from types import SimpleNamespace

    from src import config as _C
    monkeypatch.setattr(_C, "load_config", lambda: SimpleNamespace(default_model=None))
    monkeypatch.delenv("KORGEX_MODEL", raising=False)
    a = KorgexAgent()
    assert "claude" in a.model.lower()  # builtin default is Sonnet 4.6


# ── 4. MCP registration ──────────────────────────────────────────────────


def test_mcp_tool_registration_makes_it_visible(monkeypatch):
    from dataclasses import dataclass

    from src.tool_abstraction import (
        USER_TOOLS, _MCP_TOOLS, register_mcp_tool, unregister_mcp_tools,
    )

    @dataclass
    class FakeMCPTool:
        name: str
        description: str
        input_schema: dict
        server_name: str

    fake = FakeMCPTool(
        name="GitHub_create_issue",
        description="Create a GitHub issue",
        input_schema={"type": "object",
                      "properties": {"title": {"type": "string"}},
                      "required": ["title"]},
        server_name="github",
    )

    register_mcp_tool(fake)
    try:
        assert "GitHub_create_issue" in USER_TOOLS
        assert "GitHub_create_issue" in _MCP_TOOLS
        assert USER_TOOLS["GitHub_create_issue"]["_mcp_server"] == "github"
    finally:
        unregister_mcp_tools()
        assert "GitHub_create_issue" not in USER_TOOLS


def test_mcp_tool_routes_through_manager(monkeypatch):
    from dataclasses import dataclass

    from src.tool_abstraction import (
        register_mcp_tool, unregister_mcp_tools, route_tool_call,
    )

    @dataclass
    class FakeMCPTool:
        name: str
        description: str
        input_schema: dict
        server_name: str

    register_mcp_tool(FakeMCPTool(
        name="fake_mcp_echo",
        description="echo back the args",
        input_schema={"type": "object", "properties": {}},
        server_name="fake_server",
    ))

    captured = {}

    class FakeManager:
        def call_tool(self, name, args):
            captured["name"] = name
            captured["args"] = args
            return {"ok": True, "echo": args}

    monkeypatch.setattr("src.mcp_client.get_manager", lambda: FakeManager())

    try:
        result = route_tool_call("fake_mcp_echo", {"x": 1})
        assert result == {"ok": True, "echo": {"x": 1}}
        assert captured == {"name": "fake_mcp_echo", "args": {"x": 1}}
    finally:
        unregister_mcp_tools()


# ── 5. Streaming bridge wiring ───────────────────────────────────────────


def test_interactive_off_in_non_tty():
    # In pytest, stdout is not a TTY, so default interactive should be False
    a = KorgexAgent(model="claude-sonnet-4-6")
    assert a.interactive is False


def test_interactive_can_be_forced_off():
    a = KorgexAgent(model="claude-sonnet-4-6", interactive=False)
    assert a.interactive is False
    assert a._get_session() is None  # no session built when not interactive


def test_openai_assistant_turn_omits_empty_tool_calls():
    # OpenAI/OpenRouter reject `tool_calls: []` (empty array). A text-only turn
    # must omit the key entirely, not send an empty list. (Regression: error 400
    # "Invalid messages[..].tool_calls: empty array".)
    a = KorgexAgent(model="gpt-4o", interactive=False)
    assert a.provider == "openai"

    class _M:
        content = "hello"
        tool_calls = None

    class _C:
        message = _M()

    class _R:
        choices = [_C()]

    turn = a._assistant_turn(_R())
    assert turn["role"] == "assistant" and turn["content"] == "hello"
    assert "tool_calls" not in turn, f"empty tool_calls must be omitted, got {turn}"


def test_openai_assistant_turn_keeps_real_tool_calls():
    a = KorgexAgent(model="gpt-4o", interactive=False)

    class _Fn:
        name = "Read"
        arguments = '{"file_path": "x"}'

    class _TC:
        id = "call_1"
        function = _Fn()

    class _M:
        content = None
        tool_calls = [_TC()]

    class _C:
        message = _M()

    class _R:
        choices = [_C()]

    turn = a._assistant_turn(_R())
    assert turn["tool_calls"][0]["function"]["name"] == "Read"


def test_interactive_session_lazily_constructed():
    a = KorgexAgent(model="claude-sonnet-4-6", interactive=True)
    # Constructed lazily on first _get_session call
    assert a._session is None
    session = a._get_session()
    assert session is not None
    assert hasattr(session, "stream_event")
    assert hasattr(session, "spinner")


# ── 6. OpenAI streaming accumulation ─────────────────────────────────────


def test_openai_streaming_assembles_text_and_tool_calls(monkeypatch):
    """Verify _call_openai_streaming correctly accumulates chunks into a
    response shape that _extract_tool_calls + _assistant_turn understand."""
    from types import SimpleNamespace

    def chunk(content=None, tool_call_partials=None):
        tcs = []
        for p in (tool_call_partials or []):
            tcs.append(SimpleNamespace(
                index=p["index"],
                id=p.get("id"),
                function=SimpleNamespace(
                    name=p.get("name"),
                    arguments=p.get("arguments"),
                ),
            ))
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content=content, tool_calls=tcs or None,
        ))])

    # Simulate a real OpenAI stream: name + id arrive first, then JSON args
    # arrive across multiple chunks, then a final text token.
    fake_chunks = [
        chunk(tool_call_partials=[{"index": 0, "id": "call_abc", "name": "Write"}]),
        chunk(tool_call_partials=[{"index": 0, "arguments": '{"file_'}]),
        chunk(tool_call_partials=[{"index": 0, "arguments": 'path": "/tmp/x.txt", '}]),
        chunk(tool_call_partials=[{"index": 0, "arguments": '"content": "hi"}'}]),
        chunk(content="Done."),
    ]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert kwargs["stream"] is True
                    return iter(fake_chunks)

    a = KorgexAgent(model="gpt-4o-mini", interactive=True)
    response = a._call_openai_streaming(FakeClient(), messages=[], tools=[])

    # _extract_tool_calls should see a single Write call with full args
    calls = a._extract_tool_calls(response)
    assert len(calls) == 1
    assert calls[0]["name"] == "Write"
    assert calls[0]["id"] == "call_abc"
    assert calls[0]["args"] == {"file_path": "/tmp/x.txt", "content": "hi"}

    # Final text accessible via _extract_final_text
    assert a._extract_final_text(response) == "Done."


def test_openai_streaming_handles_text_only_response():
    """No tool calls — just text. Should still work."""
    from types import SimpleNamespace

    def text_chunk(s):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content=s, tool_calls=None,
        ))])

    fake = [text_chunk("Hello "), text_chunk("world"), text_chunk("!")]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return iter(fake)

    a = KorgexAgent(model="gpt-4o-mini", interactive=True)
    response = a._call_openai_streaming(FakeClient(), messages=[], tools=[])
    assert a._extract_tool_calls(response) == []
    assert a._extract_final_text(response) == "Hello world!"


# ── 7. Dashboard /api/swarm/* endpoints ──────────────────────────────────


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from src.dashboard import create_app
    app = create_app()
    assert app is not None, "FastAPI not installed"
    return TestClient(app)


def test_dashboard_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_swarm_refactor_rejects_missing_filepath(client):
    r = client.post("/api/swarm/refactor", json={})
    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False
    assert "filepath" in body["error"]


def test_swarm_heal_rejects_missing_args(client):
    r = client.post("/api/swarm/heal", json={"filepath": "x.py"})
    assert r.status_code == 400
    assert "command" in r.json()["error"]


def test_swarm_profile_rejects_missing_command(client):
    r = client.post("/api/swarm/profile", json={})
    assert r.status_code == 400
    assert "command" in r.json()["error"]


def test_swarm_refactor_returns_clean_error_without_api_key(client, monkeypatch, tmp_path):
    """When no API key is configured the endpoint should return JSON, not crash."""
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "KORGEX_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    # also neutralize any real ~/.korgex/config.json (the agent reads config now)
    monkeypatch.setenv("KORGEX_CONFIG", str(tmp_path / "empty.json"))
    r = client.post("/api/swarm/refactor", json={"filepath": "src/cli.py"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "API key" in body.get("error", "")


# ── 8. MCP integration — stub server over stdio ───────────────────────────
# This test would have caught the original bug (stdout reader never started):
# the initialize handshake would have timed out after 60s.


def test_mcp_connect_discover_call_against_stub_server():
    """Full round-trip: connect → discover → call → disconnect against a real
    subprocess MCP server. Exercises the threading wiring end-to-end."""
    from src.mcp_client import MCPClient, MCPServerConfig

    stub = Path(__file__).parent / "stub_mcp_server.py"
    config = MCPServerConfig(
        name="stub",
        command=sys.executable,
        args=[str(stub)],
        timeout=10,
    )

    client = MCPClient(config)

    result = client.connect()
    assert result.get("status") == "connected", f"connect() failed: {result}"
    assert client.is_connected()

    tools = client.discover_tools()
    assert len(tools) == 1
    assert tools[0].name == "echo"
    assert tools[0].server_name == "stub"

    call_result = client.call_tool("echo", {"text": "hello korgex"})
    assert "error" not in call_result, f"call_tool failed: {call_result}"
    content = call_result.get("content", [])
    echoed = json.loads(content[0]["text"])
    assert echoed == {"text": "hello korgex"}

    client.disconnect()
    assert not client.is_connected()


# ── 9. Journal write → on-disk → ledger_spec.verify_chain round-trip ───────
# Closes the gap: until now NOTHING took events through the real production
# write path (LocalJournalClient._append, which stamps prev_hash/entry_hash via
# ledger_spec.chain_hash) and then re-verified the bytes ON DISK with the spec
# verifier. The conformance vectors are hand-stamped by a test helper, not by
# the client — so a divergence between how the client writes and how the spec
# verifies would never be caught. These tests run the round-trip end-to-end,
# and deliberately push NON-BMP / surrogate-pair content through it, the exact
# code path most likely to silently diverge.


def test_local_journal_roundtrips_through_verify_chain(tmp_path, monkeypatch):
    from src import ledger_spec as S
    from src.korg_ledger import LocalJournalClient

    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)  # SHA-256 path
    jp = tmp_path / "journal.jsonl"
    c = LocalJournalClient(journal_path=str(jp))
    s1 = c.record_user_prompt("add a function")
    s2 = c.record_llm_call(model="m", prompt_tokens=10, completion_tokens=4,
                           duration_ms=12, triggered_by=s1)
    c.record_tool_call("Write", {"path": "a.py"}, {"ok": True}, True, 3, triggered_by=s2)

    # Re-read the bytes that actually landed on disk and verify with the SPEC
    # module (not the client's own re-export) — proves write and verify agree.
    on_disk = [json.loads(line) for line in jp.read_text().splitlines() if line.strip()]
    assert len(on_disk) == 3
    assert S.verify_chain(on_disk) == [], "freshly written journal must verify clean"
    assert S.verify_dag(on_disk) == []


def test_local_journal_roundtrips_non_bmp_content_through_verify(tmp_path, monkeypatch):
    """Surrogate-pair content survives the full write→disk→verify round-trip.

    A U+10000+ codepoint canonicalizes to a UTF-16 surrogate pair; if the client
    wrote the file in a way that didn't match ledger_spec.canonicalize (e.g. raw
    UTF-8 vs \\uXXXX), the on-disk entry_hash would fail re-verification. This is
    the production-path twin of the nonbmp-intact conformance vector."""
    from src import ledger_spec as S
    from src.korg_ledger import LocalJournalClient

    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)  # SHA-256 path
    jp = tmp_path / "journal.jsonl"
    c = LocalJournalClient(journal_path=str(jp))
    s1 = c.record_user_prompt("make it \U0001F600 in 中文 \U00010000")  # emoji + CJK + astral
    c.record_tool_call("Write",
                       {"path": "中文.py", "snippet": "# \U0001F600 \U00010000"},
                       {"ok": True, "note": "done \U0001F4A9"}, True, 4, triggered_by=s1)

    raw = jp.read_bytes()
    # The on-disk journal is itself ASCII (\uXXXX-escaped) — byte-stable cross-platform.
    assert raw.isascii(), "journal on disk must be ASCII-only (escaped non-BMP)"
    assert b"\\ud83d\\ude00" in raw, "expected the U+1F600 surrogate pair on disk"

    on_disk = [json.loads(line) for line in jp.read_text().splitlines() if line.strip()]
    # The decisive assertion: the spec verifier reproduces the client's hashes
    # over surrogate-pair content, byte-for-byte.
    assert S.verify_chain(on_disk) == [], "non-BMP journal failed re-verification"
    assert S.verify_dag(on_disk) == []
    # And the non-BMP payload survived the round-trip intact (not mangled).
    assert on_disk[0]["args"]["prompt"] == "make it \U0001F600 in 中文 \U00010000"


def test_local_journal_non_bmp_tamper_is_detected(tmp_path, monkeypatch):
    """Flipping one non-BMP codepoint in a persisted line breaks the chain."""
    from src import ledger_spec as S
    from src.korg_ledger import LocalJournalClient

    monkeypatch.delenv("KORG_LEDGER_HMAC_KEY", raising=False)  # SHA-256 path
    jp = tmp_path / "journal.jsonl"
    c = LocalJournalClient(journal_path=str(jp))
    c.record_user_prompt("emoji \U0001F600")
    c.record_tool_call("Write", {"path": "x.py"}, {"ok": True}, True, 1, triggered_by=1)

    lines = jp.read_text().splitlines()
    obj = json.loads(lines[0])
    obj["args"]["prompt"] = "emoji \U0001F4A9"  # swap grinning-face for pile-of-poo
    lines[0] = json.dumps(obj)
    jp.write_text("\n".join(lines) + "\n")

    on_disk = [json.loads(line) for line in jp.read_text().splitlines() if line.strip()]
    errors = S.verify_chain(on_disk)
    assert errors, "a flipped non-BMP codepoint must break the hash-chain"
    assert any("seq 1" in e for e in errors)
