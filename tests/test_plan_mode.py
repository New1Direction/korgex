"""Plan mode — read-only enforcement + the plan→approve→execute lifecycle.

In plan mode the agent may ONLY read/search and write its plan to a single plan
file; every other side-effecting tool (Edit, Bash, arbitrary Write, …) is blocked
until the user approves the plan. These tests pin the pure gate + the lifecycle
state machine; the approval UI is a thin shell over them.
"""
from src import plan_mode as PM


# ── read-only gate ─────────────────────────────────────────────────────────────

def test_read_tools_allowed_in_plan_mode():
    for name in ("Read", "Grep", "Glob", "ToolSearch", "Recall", "BusInbox"):
        assert PM.is_blocked(name, {}, plan_path="PLAN.md") is None, f"{name} should be allowed"


def test_mutating_tools_blocked_in_plan_mode():
    for name, args in [
        ("Edit", {"file_path": "src/foo.py"}),
        ("MultiEdit", {"file_path": "src/foo.py"}),
        ("NotebookEdit", {"notebook_path": "x.ipynb"}),
        ("Bash", {"command": "rm -rf build"}),
    ]:
        block = PM.is_blocked(name, args, plan_path="PLAN.md")
        assert block is not None and "plan mode" in block["error"].lower()


def test_writing_the_plan_file_is_allowed():
    assert PM.is_blocked("Write", {"file_path": "PLAN.md"}, plan_path="PLAN.md") is None


def test_writing_any_other_file_is_blocked():
    block = PM.is_blocked("Write", {"file_path": "src/foo.py"}, plan_path="PLAN.md")
    assert block is not None and "plan mode" in block["error"].lower()


def test_plan_path_match_is_basename_tolerant():
    # An absolute plan path still matches a relative write to the same file.
    assert PM.is_blocked("Write", {"file_path": "/repo/PLAN.md"},
                         plan_path="/repo/PLAN.md") is None


# ── lifecycle state machine ─────────────────────────────────────────────────────

def test_lifecycle_starts_in_planning():
    st = PM.PlanState()
    assert st.phase == "planning"
    assert st.is_planning() and not st.is_executing()


def test_approve_moves_to_executing():
    st = PM.PlanState()
    st.apply("approve")
    assert st.phase == "executing" and st.is_executing()


def test_revise_stays_in_planning():
    st = PM.PlanState()
    st.apply("revise")
    assert st.phase == "planning" and st.is_planning()


def test_abandon_ends():
    st = PM.PlanState()
    st.apply("abandon")
    assert st.phase == "abandoned"
    assert not st.is_planning() and not st.is_executing()


def test_unknown_action_is_ignored_stays_planning():
    st = PM.PlanState()
    st.apply("garbage")
    assert st.phase == "planning"


def test_parse_approval_input():
    assert PM.parse_approval("approve") == "approve"
    assert PM.parse_approval("a") == "approve"
    assert PM.parse_approval("revise this") == "revise"
    assert PM.parse_approval("r") == "revise"
    assert PM.parse_approval("abandon") == "abandon"
    assert PM.parse_approval("q") == "abandon"
    assert PM.parse_approval("mumble") is None


# ── integration: plan mode through the agent gate ──────────────────────────────

class _Led:
    def __init__(self): self.events = []
    def record_tool_call(self, **kw): self.events.append(kw); return len(self.events)
    def record_user_prompt(self, p, triggered_by=None): return 1
    def record_llm_call(self, **kw): return 1


def _pm_ctx(plan_mode_active, plan_path="/tmp/PLAN.md"):
    from src.tool_gate import GateContext
    return GateContext(
        workspace_root=None, protected_paths=None, edit_policy="free",
        plan_mode_active=plan_mode_active, plan_path=plan_path,
        repo_root="/tmp", interactive=False, mcp_tools=None,
        checkpoint=lambda p: None, confirmer=None,
        classify_edit=lambda c, p: (True, "allow", ""))


def test_agent_plan_mode_blocks_edit_but_allows_plan_write(tmp_path):
    from src.agent import KorgexAgent
    from src.tool_gate import PlanModeGate
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False, mode="plan")
    assert a.plan_mode_active is True  # mode="plan" turns it on

    led = _Led()
    ctx = a._gate_context()

    # an Edit to source is blocked while planning
    out = PlanModeGate().evaluate(
        {"id": "1", "name": "Edit", "args": {"file_path": str(tmp_path / "foo.py")}}, ctx)
    assert out.blocked and "plan mode" in out.block_result["error"].lower()
    # forward the intent to the led for the assertion
    if out.record:
        led.record_tool_call(
            tool_name=out.record.tool_name, args=out.record.args,
            result=out.record.result, success=out.record.success,
            duration_ms=0, triggered_by=1)
    assert any(e["tool_name"] == "plan_mode.block" for e in led.events)

    # writing the plan file is allowed
    out2 = PlanModeGate().evaluate(
        {"id": "2", "name": "Write", "args": {"file_path": a.plan_path}}, ctx)
    assert not out2.blocked

    # after approval, the edit goes through the plan gate
    a.approve_plan()
    assert a.plan_mode_active is False
    ctx3 = a._gate_context()
    out3 = PlanModeGate().evaluate(
        {"id": "3", "name": "Edit", "args": {"file_path": str(tmp_path / "foo.py")}}, ctx3)
    assert not out3.blocked


def test_agent_not_in_plan_mode_by_default(tmp_path):
    from src.agent import KorgexAgent
    from src.tool_gate import PlanModeGate
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    assert a.plan_mode_active is False
    # nothing blocked when plan mode is off
    ctx = a._gate_context()
    out = PlanModeGate().evaluate(
        {"id": "1", "name": "Bash", "args": {"command": "ls"}}, ctx)
    assert not out.blocked
