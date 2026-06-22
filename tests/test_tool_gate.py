from dataclasses import dataclass
from src import tool_gate as tg
from src.tool_gate import GateOutcome, LedgerIntent, ALLOW


@dataclass
class _FakeGate:
    name: str
    outcome: GateOutcome
    def evaluate(self, call, ctx):
        return self.outcome


def _ctx():
    # minimal ctx; Task 1 gates ignore it
    return tg.GateContext(
        workspace_root=None, protected_paths=None, edit_policy="free",
        plan_mode_active=False, plan_path=None, repo_root="/tmp",
        interactive=False, mcp_tools=None,
        checkpoint=lambda p: None, confirmer=None,
        classify_edit=lambda c, p: (True, "allow", ""))


def test_allow_passthrough_records_nothing():
    seen = []
    out, call = tg.evaluate({"id": "1", "name": "Read", "args": {}}, _ctx(),
                            seen.append, gates=(_FakeGate("g", ALLOW),))
    assert out.blocked is False
    assert seen == []


def test_first_block_wins_and_stops():
    blk = GateOutcome(blocked=True, block_result={"error": "no"},
                      record=LedgerIntent("g1.block", {}, {"v": "X"}, False))
    later = _FakeGate("g2", GateOutcome(record=LedgerIntent("g2", {}, {}, True)))
    seen = []
    out, _ = tg.evaluate({"id": "1", "name": "Bash", "args": {}}, _ctx(),
                         seen.append, gates=(_FakeGate("g1", blk), later))
    assert out.blocked is True
    assert out.block_result == {"error": "no"}
    assert [i.tool_name for i in seen] == ["g1.block"]  # g2 never ran


def test_record_fires_on_allow():
    allow_rec = GateOutcome(record=LedgerIntent("egress.flag", {}, {"v": "ok"}, True))
    seen = []
    out, _ = tg.evaluate({"id": "1", "name": "WebFetch", "args": {}}, _ctx(),
                         seen.append, gates=(_FakeGate("eg", allow_rec),))
    assert out.blocked is False
    assert [i.tool_name for i in seen] == ["egress.flag"]


def test_new_args_swapped_immutably():
    redacted = {"command": "curl --data REDACTED host"}
    g = _FakeGate("eg", GateOutcome(new_args=redacted))
    original = {"id": "1", "name": "Bash", "args": {"command": "curl --data SECRET host"}}
    out, call = tg.evaluate(original, _ctx(), [].append, gates=(g,))
    assert call["args"] == redacted
    assert original["args"] == {"command": "curl --data SECRET host"}  # not mutated
