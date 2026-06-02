"""
Structured-output tests — schema-constrained final answers (roadmap P0).

Verifies:
1. build_request_kwargs emits the right provider-specific shape (Anthropic forced
   tool_use vs OpenAI response_format json_schema).
2. extract pulls the structured object out of either provider's response shape.
3. validate enforces the JSON Schema client-side (the real guarantee korgex owns).
4. run_task(output_schema=...) returns a validated object, retries once on a
   schema-invalid reply, and records the validated object onto the ledger event.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.structured_output import (  # noqa: E402
    STRUCTURED_TOOL_NAME, build_request_kwargs, extract, validate,
)
from src.agent import KorgexAgent  # noqa: E402


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "files_changed"],
    "properties": {
        "summary": {"type": "string"},
        "files_changed": {"type": "integer"},
    },
}


# ── 1. build_request_kwargs ───────────────────────────────────────────────

def test_build_request_anthropic_forces_output_tool():
    kw = build_request_kwargs(SCHEMA, "anthropic")
    assert kw["tool_choice"] == {"type": "tool", "name": STRUCTURED_TOOL_NAME}
    assert len(kw["tools"]) == 1
    tool = kw["tools"][0]
    assert tool["name"] == STRUCTURED_TOOL_NAME
    assert tool["input_schema"] == SCHEMA


def test_build_request_openai_uses_response_format():
    kw = build_request_kwargs(SCHEMA, "openai")
    rf = kw["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == SCHEMA
    assert "tool_choice" not in kw  # OpenAI doesn't force a tool for this


# ── 2. extract ────────────────────────────────────────────────────────────

def test_extract_anthropic_from_tool_use_block():
    obj = {"summary": "done", "files_changed": 2}
    resp = SimpleNamespace(content=[
        SimpleNamespace(type="text", text="ignore me"),
        SimpleNamespace(type="tool_use", name=STRUCTURED_TOOL_NAME, input=obj),
    ])
    assert extract(resp, "anthropic") == obj


def test_extract_openai_parses_json_content():
    obj = {"summary": "ok", "files_changed": 1}
    resp = SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content=json.dumps(obj)))
    ])
    assert extract(resp, "openai") == obj


def test_extract_returns_none_on_garbage():
    resp = SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content="not json"))
    ])
    assert extract(resp, "openai") is None


# ── 3. validate ───────────────────────────────────────────────────────────

def test_validate_accepts_conforming_object():
    assert validate({"summary": "x", "files_changed": 3}, SCHEMA) == []


def test_validate_reports_errors_for_bad_object():
    errors = validate({"summary": "x", "files_changed": "three"}, SCHEMA)
    assert errors  # files_changed should be integer, not string


def test_validate_reports_missing_required():
    errors = validate({"summary": "x"}, SCHEMA)
    assert any("files_changed" in e for e in errors)


# ── 4. run_task(output_schema=...) integration ────────────────────────────

class _FakeLedger:
    def __init__(self):
        self.events = []

    def record_user_prompt(self, prompt, triggered_by=None):
        self.events.append({"kind": "user_prompt", "triggered_by": triggered_by})
        return 1

    def record_llm_call(self, **kw):
        self.events.append({"kind": "llm", **kw})
        return 100 + len([e for e in self.events if e["kind"] == "llm"])

    def record_tool_call(self, **kw):
        self.events.append({"kind": "tool", **kw})
        return None


def _openai_text(text):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
    )


def _openai_json(obj):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(obj), tool_calls=None))],
    )


class _ScriptedAgent(KorgexAgent):
    """KorgexAgent whose _call replays a scripted list of responses."""

    def __init__(self, responses, **kw):
        kw.setdefault("model", "gpt-4o")
        kw.setdefault("interactive", False)
        super().__init__(**kw)
        self._responses = list(responses)
        self.calls = []
        self.ledger = _FakeLedger()

    def _get_client(self):
        return object()

    def _call(self, client, messages, tools, output_schema=None, system_prompt=None, system_volatile=None):
        self.calls.append({"output_schema": output_schema})
        return self._responses.pop(0)


def test_run_task_with_schema_returns_validated_object():
    obj = {"summary": "added endpoint", "files_changed": 2}
    # First call: prose, no tools (loop terminates). Second call: structured pass.
    agent = _ScriptedAgent([_openai_text("All done."), _openai_json(obj)])
    result = agent.run_task("do it", output_schema=SCHEMA)

    assert result["success"] is True
    assert result["result"] == obj
    # The structured pass must have been made with the schema set.
    assert any(c["output_schema"] == SCHEMA for c in agent.calls)
    # The validated object must land on a ledger llm event as assistant_text.
    def _as_obj(t):
        try:
            return json.loads(t)
        except Exception:
            return None
    llm_texts = [e.get("assistant_text") for e in agent.ledger.events if e["kind"] == "llm"]
    assert any(_as_obj(t) == obj for t in llm_texts if t)


def test_run_task_with_schema_retries_once_on_invalid_then_fails_cleanly():
    bad = {"summary": "x", "files_changed": "not-an-int"}
    # prose terminator, then two invalid structured replies → one retry, then fail
    agent = _ScriptedAgent([_openai_text("done"), _openai_json(bad), _openai_json(bad)])
    result = agent.run_task("do it", output_schema=SCHEMA)

    assert result["success"] is False
    assert "validation" in result["result"].lower() or "schema" in result["result"].lower()
    # Two structured attempts were made (original + one retry)
    schema_calls = [c for c in agent.calls if c["output_schema"] == SCHEMA]
    assert len(schema_calls) == 2


def test_run_task_without_schema_unchanged_returns_prose():
    agent = _ScriptedAgent([_openai_text("plain answer")])
    result = agent.run_task("hello")
    assert result["success"] is True
    assert result["result"] == "plain answer"
    # No structured pass when no schema given
    assert all(c["output_schema"] is None for c in agent.calls)
