"""Tests for the korgex `--introspect` document.

The cross-ecosystem invariant: every korg adapter / binary that emits
an introspect document MUST use:
  - schema = "korg:introspect@v1"
  - the same Capabilities field set
  - string-keyed exit_codes table
  - dot-namespaced command IDs (<binary>.<command>)

These tests pin those invariants so a future change here can't silently
drift away from thumper, korg, and recall-mcp.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

from src.introspect import (
    BINARY_NAME,
    EXIT_CODES,
    INTROSPECT_SCHEMA_ID,
    Callable,
    Capabilities,
    build_document,
    emit,
    get_callables,
)


# ── Capabilities ──────────────────────────────────────────────────────


def test_capabilities_defaults_are_safe():
    c = Capabilities()
    assert c.side_effects == "none"
    assert c.requires_project is False
    assert c.long_running is False
    assert c.stateful is False
    assert c.reads_stdin is False
    assert c.supports_output_path is False


def test_capabilities_is_frozen():
    c = Capabilities()
    with pytest.raises(Exception):
        c.side_effects = "fs_write"  # type: ignore[misc]


def test_capabilities_to_dict_includes_all_fields():
    d = Capabilities().to_dict()
    for key in (
        "output_mode",
        "side_effects",
        "requires_project",
        "long_running",
        "stateful",
        "reads_stdin",
        "supports_output_path",
    ):
        assert key in d, f"Capabilities missing wire field {key}"


# ── Callable list ─────────────────────────────────────────────────────


def test_callables_have_unique_ids():
    ids = [c.id for c in get_callables()]
    assert len(set(ids)) == len(ids), f"duplicate command_ids: {ids}"


def test_command_ids_are_dot_namespaced():
    for c in get_callables():
        assert c.id.startswith("korgex."), f"id must start with 'korgex.': {c.id}"
        assert " " not in c.id


def test_recognized_side_effects():
    valid = {"none", "fs_read", "fs_write", "network", "ledger_write"}
    for c in get_callables():
        assert (
            c.capabilities.side_effects in valid
        ), f"unknown side_effects on {c.id}: {c.capabilities.side_effects}"


def test_recognized_output_modes():
    valid = {"none", "stream", "envelope", "session"}
    for c in get_callables():
        assert (
            c.capabilities.output_mode in valid
        ), f"unknown output_mode on {c.id}: {c.capabilities.output_mode}"


def test_input_schemas_are_object_typed():
    for c in get_callables():
        assert c.input_schema.get("type") == "object", (
            f"input_schema must be 'type: object' for {c.id}"
        )


def test_long_running_stateful_uses_session_or_stream_output():
    """A long-running stateful callable shouldn't claim envelope mode —
    envelope means one final wrapped result, which contradicts both."""
    for c in get_callables():
        cap = c.capabilities
        if cap.long_running and cap.stateful:
            assert cap.output_mode in {"session", "stream", "none"}, (
                f"{c.id} long_running+stateful but output_mode={cap.output_mode}"
            )


def test_agent_is_top_level_callable():
    ids = {c.id for c in get_callables()}
    assert "korgex.agent" in ids, "the default agent invocation should be a declared callable"


# ── Document ──────────────────────────────────────────────────────────


def test_document_has_schema_tag():
    doc = build_document("0.3.2")
    assert doc["schema"] == INTROSPECT_SCHEMA_ID
    assert doc["schema"] == "korg:introspect@v1"


def test_document_carries_binary_and_version():
    doc = build_document("9.9.9")
    assert doc["binary"] == BINARY_NAME == "korgex"
    assert doc["version"] == "9.9.9"
    assert doc["callables_declared"] is True


def test_document_round_trips_through_json():
    doc = build_document("0.3.2")
    blob = json.dumps(doc, indent=2)
    parsed = json.loads(blob)
    assert parsed == doc
    # Specifically: exit_codes keys must be strings on the wire
    assert all(isinstance(k, str) for k in parsed["exit_codes"].keys())


def test_document_exit_codes_table():
    doc = build_document("0.3.2")
    assert doc["exit_codes"]["0"] == "success"
    # Every code must be string-keyed and valid integer
    for key in doc["exit_codes"].keys():
        assert isinstance(key, str)
        assert key.isdigit()


def test_exit_codes_python_table_is_int_keyed():
    assert all(isinstance(k, int) for k in EXIT_CODES.keys())
    assert EXIT_CODES[0] == "success"


# ── Cross-adapter invariants ──────────────────────────────────────────


def test_uses_canonical_schema_id():
    """Cross-ecosystem invariant: this schema ID is shared with thumper,
    korg, recall-mcp. If we ever bump it, every adapter must bump in lockstep."""
    assert INTROSPECT_SCHEMA_ID == "korg:introspect@v1"


def test_side_effects_vocabulary_matches_other_adapters():
    """The recognized side_effects strings must match the other adapters
    so cross-binary agents can switch on them."""
    used = {c.capabilities.side_effects for c in get_callables()}
    expected_vocabulary = {"none", "fs_read", "fs_write", "network", "ledger_write"}
    # Every actual value must be from the shared vocabulary
    assert used.issubset(expected_vocabulary)


# ── emit / CLI integration ────────────────────────────────────────────


def test_emit_writes_valid_json_to_stdout():
    buf = io.StringIO()
    with redirect_stdout(buf):
        emit("0.3.2")
    parsed = json.loads(buf.getvalue())
    assert parsed["schema"] == "korg:introspect@v1"
    assert parsed["binary"] == "korgex"
    assert isinstance(parsed["callables"], list)


def test_cli_introspect_short_circuits_before_parser():
    """End-to-end: `python -m src.cli --introspect` returns a valid
    document without any args / config / network requirements."""
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "--introspect"],
        capture_output=True,
        text=True,
        cwd="/Users/clubpenguin/Documents/korgex",
    )
    assert result.returncode == 0, f"failed: stderr={result.stderr!r}"
    doc = json.loads(result.stdout)
    assert doc["schema"] == "korg:introspect@v1"
    assert doc["binary"] == "korgex"
    # Sanity: the document has the agent callable
    callable_ids = {c["command_id"] for c in doc["callables"]}
    assert "korgex.agent" in callable_ids
