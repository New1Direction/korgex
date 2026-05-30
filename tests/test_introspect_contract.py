"""Cross-language CONTRACT test for the `korg:introspect@v1` document.

`test_introspect.py` pins the *Python-side* invariants. This file pins the
*cross-language* contract: a frozen, checked-in snapshot of the wire shape that
the TypeScript adapter (`Korg/adapters/introspect-mcp-ts/src/discovery.ts`,
`validateDocument`) actually consumes. The intent is that Python and the TS
adapters fail *in lockstep* on schema drift instead of fracturing silently.

The silent-fracture trap this guards against:

  * `discovery.ts::validateDocument` hard-requires, per callable, the field set
    {command_id, name, input_schema, capabilities}. If the Python side renamed
    `command_id` → `id` on the wire, or dropped `capabilities`, every Python
    `test_introspect.py` test could still pass while the TS adapter throws
    `DiscoveryError` at runtime — a silent fracture across the ecosystem.
  * `safety.ts::ALL_EFFECTS` switches the safe-by-default gate on the exact
    side_effects vocabulary. A new side_effects value emitted by Python that the
    TS gate doesn't know is a security-relevant divergence.
  * `discovery.ts` keys the binary off `schema === "korg:introspect@v1"`.
    A schema bump on one side without the other is a hard break.

The contract is frozen in `spec/korg-introspect-v1/contract.json` so the assertion
is authoritative, not self-referential: a human reviews a diff to that file
whenever the wire shape legitimately changes, and the same file is the oracle the
TS adapter's own contract test reads (mirroring the `korg-ledger@v1` pattern in
`tests/test_ledger_conformance.py`).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.introspect import build_document  # noqa: E402

CONTRACT_PATH = os.path.join(ROOT, "spec", "korg-introspect-v1", "contract.json")


def _load_contract() -> dict:
    with open(CONTRACT_PATH) as f:
        return json.load(f)


# ── the contract file exists and is well-formed ─────────────────────────────


def test_contract_file_present():
    assert os.path.isfile(CONTRACT_PATH), (
        "spec/korg-introspect-v1/contract.json missing — this frozen file is the "
        "cross-language oracle the TS adapter contract test also reads."
    )
    c = _load_contract()
    assert c["schema_id"] == "korg:introspect@v1"
    assert c["spec_version"] == "korg-introspect@v1"


# ── document-level shape is frozen ──────────────────────────────────────────


def test_document_top_level_keys_match_contract():
    """The set of top-level keys on the wire is frozen. Adding or removing one
    is a wire change that must update the contract (and the TS DiscoveredBinary
    interface) in lockstep."""
    doc = build_document("0.0.0")
    contract = _load_contract()
    assert sorted(doc.keys()) == sorted(contract["document_keys"]), (
        "top-level introspect keys drifted from the frozen contract; "
        "update spec/korg-introspect-v1/contract.json AND the TS "
        "DiscoveredBinary interface together"
    )


def test_schema_id_matches_contract():
    doc = build_document("0.0.0")
    contract = _load_contract()
    # This is exactly the equality discovery.ts::validateDocument enforces:
    #   if (schema !== SUPPORTED_SCHEMA) throw DiscoveryError(...)
    assert doc["schema"] == contract["schema_id"]


def test_callables_declared_is_boolean_true():
    # discovery.ts does Boolean(d["callables_declared"]); korgex declares them.
    doc = build_document("0.0.0")
    assert doc["callables_declared"] is True


# ── per-callable required fields — the TS validateDocument hard requirement ──


def test_every_callable_has_ts_required_fields():
    """discovery.ts::validateDocument throws if any callable is missing one of
    {command_id, name, input_schema, capabilities}. Pin that exact set so a
    Python-side rename/drop fails HERE, in the same place the TS adapter would."""
    doc = build_document("0.0.0")
    contract = _load_contract()
    required = contract["callable_required_fields"]
    assert required == ["capabilities", "command_id", "input_schema", "name"], (
        "the TS-required callable field set is frozen; if you change it you must "
        "change discovery.ts::validateDocument's required-loop in lockstep"
    )
    for i, c in enumerate(doc["callables"]):
        for field in required:
            assert field in c, (
                f"callables[{i}] ({c.get('command_id', '?')}) missing TS-required "
                f"field {field!r} — TS validateDocument would throw DiscoveryError"
            )


def test_callable_command_ids_match_frozen_set():
    """The full set of exposed command_ids is frozen. Adding or removing a
    callable is a contract change a human reviews via the contract diff."""
    doc = build_document("0.0.0")
    contract = _load_contract()
    actual = sorted(c["command_id"] for c in doc["callables"])
    assert actual == sorted(contract["command_ids"]), (
        "exposed command_ids drifted from the frozen contract; "
        "update spec/korg-introspect-v1/contract.json"
    )


def test_command_ids_are_unique_on_the_wire():
    # discovery.ts throws DiscoveryError on a duplicate command_id.
    doc = build_document("0.0.0")
    ids = [c["command_id"] for c in doc["callables"]]
    assert len(set(ids)) == len(ids), f"duplicate command_id on the wire: {ids}"


# ── shared vocabularies must match the TS adapter's hard-coded sets ─────────


def test_side_effects_vocabulary_matches_ts_all_effects():
    """safety.ts::ALL_EFFECTS is the exact set the TS safe-by-default gate
    switches on. Every side_effects value Python emits must be in it, and the
    frozen contract vocabulary must equal it."""
    doc = build_document("0.0.0")
    contract = _load_contract()
    vocab = set(contract["side_effects_vocabulary"])
    # Frozen against the TS ALL_EFFECTS set verbatim.
    assert vocab == {"none", "fs_read", "fs_write", "network", "ledger_write"}, (
        "side_effects vocabulary drifted from safety.ts::ALL_EFFECTS"
    )
    for c in doc["callables"]:
        se = c["capabilities"]["side_effects"]
        assert se in vocab, (
            f"{c['command_id']} emits side_effects={se!r} which the TS safety "
            f"gate (safety.ts::ALL_EFFECTS) does not know — safe-by-default "
            f"gating would silently diverge"
        )


def test_output_mode_vocabulary_matches_contract():
    doc = build_document("0.0.0")
    contract = _load_contract()
    vocab = set(contract["output_mode_vocabulary"])
    assert vocab == {"none", "stream", "envelope", "session"}
    for c in doc["callables"]:
        assert c["capabilities"]["output_mode"] in vocab, (
            f"{c['command_id']} output_mode not in frozen vocabulary"
        )


def test_capabilities_keys_match_contract():
    """Every callable's capabilities object exposes exactly the frozen key set.
    The TS invoker/safety code reads capabilities.output_mode and
    capabilities.side_effects; pinning the whole object guards undeclared keys
    from appearing (or declared ones from vanishing) silently."""
    doc = build_document("0.0.0")
    contract = _load_contract()
    expected = sorted(contract["capabilities_keys"])
    for c in doc["callables"]:
        assert sorted(c["capabilities"].keys()) == expected, (
            f"{c['command_id']} capabilities keys drifted from frozen contract"
        )


# ── exit_codes wire form — string keys (JSON has no int keys) ───────────────


def test_exit_codes_are_string_keyed_per_contract():
    """discovery.ts types exit_codes as Record<string,string>. Python's int
    table must stringify on the wire; the success row is frozen."""
    doc = build_document("0.0.0")
    contract = _load_contract()
    for k, v in doc["exit_codes"].items():
        assert isinstance(k, str) and k.isdigit(), f"exit_codes key {k!r} not a string int"
        assert isinstance(v, str)
    assert doc["exit_codes"]["0"] == "success"
    # the frozen exit_codes table is reproduced byte-for-byte
    assert doc["exit_codes"] == contract["exit_codes"], (
        "exit_codes table drifted from the frozen contract"
    )


# ── the document survives a json round-trip with no information loss ─────────


def test_document_json_round_trips_losslessly():
    doc = build_document("1.2.3")
    assert json.loads(json.dumps(doc)) == doc


# ── meta: the contract really matches what build_document emits today ────────
# This is the non-circular anchor: if someone edits contract.json to disagree
# with the live document, exactly one of the above tests fails AND the spec
# diff is visible in review. The contract is never auto-derived at test time.


@pytest.mark.parametrize(
    "field", ["schema_id", "document_keys", "command_ids", "exit_codes"]
)
def test_contract_fields_are_explicitly_declared(field):
    contract = _load_contract()
    assert field in contract, f"contract.json missing declared field {field!r}"
