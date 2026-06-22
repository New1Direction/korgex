"""ToolGate — one deep seam every tool call crosses before it runs.

Replaces the gate sequence (workspace -> guardrail -> command_guard -> egress
-> plan_mode -> edit_policy [-> PreToolUse hook]) that was copy-pasted across
three call sites in agent.py. Each gate is an adapter over an existing decision
module; gates return DATA (GateOutcome + LedgerIntent), the pipeline records via
an injected sink and applies redacted args immutably. First block wins.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class LedgerIntent:
    """What a gate wants recorded. `triggered_by` is supplied by the sink."""
    tool_name: str
    args: dict
    result: dict
    success: bool


@dataclass(frozen=True)
class GateOutcome:
    """A gate's verdict. `record` fires on allow OR block. `new_args` rewrites
    the call payload (egress redact), applied immutably by the pipeline."""
    blocked: bool = False
    block_result: dict | None = None
    new_args: dict | None = None
    record: LedgerIntent | None = None


ALLOW = GateOutcome()  # the common pass-through: not blocked, records nothing


@dataclass(frozen=True)
class GateContext:
    """Frozen per-turn snapshot of the agent state gates read, plus injected
    capability callables for the effectful/model bits. The seam that makes gates
    testable without an agent."""
    workspace_root: str | None
    protected_paths: object
    edit_policy: str
    plan_mode_active: bool
    plan_path: str | None
    repo_root: str
    interactive: bool
    mcp_tools: object
    checkpoint: Callable[[str], "str | None"]
    confirmer: Callable | None
    classify_edit: Callable[[dict, str], tuple]


class Gate(Protocol):
    name: str
    def evaluate(self, call: dict, ctx: GateContext) -> GateOutcome: ...


GATES: tuple[Gate, ...] = ()  # populated by later tasks, in safety order


def evaluate(
    call: dict,
    ctx: GateContext,
    record: Callable[[LedgerIntent], None],
    gates: tuple[Gate, ...] = GATES,
) -> tuple[GateOutcome, dict]:
    """Run each gate in order. Record every outcome via `record`, apply any
    rewritten args immutably, stop at the first block. Returns (outcome,
    effective_call) — effective_call carries redacted args for the caller to run.
    `gates` is a test-only internal seam; production uses the module GATES."""
    for g in gates:
        out = g.evaluate(call, ctx)
        if out.record is not None:
            record(out.record)
        if out.new_args is not None:
            call = {**call, "args": out.new_args}
        if out.blocked:
            return out, call
    return ALLOW, call
