"""ToolGate — one deep seam every tool call crosses before it runs.

Replaces the gate sequence (workspace -> guardrail -> command_guard -> egress
-> plan_mode -> edit_policy [-> PreToolUse hook]) that was copy-pasted across
three call sites in agent.py. Each gate is an adapter over an existing decision
module; gates return DATA (GateOutcome + LedgerIntent), the pipeline records via
an injected sink and applies redacted args immutably. First block wins.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Protocol

from src.workspace import path_within
from src.guardrails import is_protected
from src import command_guard as _cmd_guard
from src import edit_policy as _EP


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


_WRITE_TOOLS = ("Write", "Edit")


class WorkspaceGate:
    """Gate A: enforce workspace isolation. Blocks Write/Edit outside the
    workspace root, if one is set."""
    name = "workspace"

    def evaluate(self, call: dict, ctx: GateContext) -> GateOutcome:
        if not ctx.workspace_root or call.get("name") not in _WRITE_TOOLS:
            return ALLOW
        path = (call.get("args") or {}).get("file_path")
        if not path or path_within(ctx.workspace_root, path):
            return ALLOW
        result = {
            "error": "blocked: write outside the isolated workspace",
            "verdict": "WORKSPACE_VIOLATION",
            "reason": f"{path} resolves outside workspace_root {ctx.workspace_root}",
        }
        return GateOutcome(
            blocked=True, block_result=result,
            record=LedgerIntent("workspace.guard",
                                {"tool": call["name"], "path": path}, result, False))


class GuardrailGate:
    """Gate G: protect guardrail-critical files. Blocks Write/Edit to paths
    marked as protected."""
    name = "guardrail"

    def evaluate(self, call: dict, ctx: GateContext) -> GateOutcome:
        if not ctx.protected_paths or call.get("name") not in _WRITE_TOOLS:
            return ALLOW
        path = (call.get("args") or {}).get("file_path")
        if not path or not is_protected(path, ctx.protected_paths):
            return ALLOW
        result = {
            "error": "blocked: editing a guardrail-critical file requires human approval",
            "verdict": "PROTECTED_PATH",
            "reason": f"{path} is a protected guardrail file (Gate G)",
        }
        return GateOutcome(
            blocked=True, block_result=result,
            record=LedgerIntent("guardrail.block",
                                {"tool": call["name"], "path": path}, result, False))


class CommandGuardGate:
    """Gate C: destructive-command safety floor for Bash. Bash-only; OFF under
    BYPASS and KORGEX_COMMAND_GUARD=off; fails open (any exception → ALLOW).
    Records command_guard.block on block only."""
    name = "command_guard"

    def evaluate(self, call: dict, ctx: GateContext) -> GateOutcome:
        if call.get("name") != "Bash" or ctx.edit_policy == _EP.BYPASS:
            return ALLOW
        if os.environ.get("KORGEX_COMMAND_GUARD", "on").strip().lower() in (
                "0", "false", "no", "off"):
            return ALLOW
        command = (call.get("args") or {}).get("command", "") or ""
        try:
            verdict = _cmd_guard.assess_command(command)
        except Exception:
            return ALLOW  # fail-open — a safety floor must never break the loop
        if not verdict:
            return ALLOW
        result = {
            "error": f"blocked: {verdict['category']} — {verdict['reason']}",
            "verdict": "DESTRUCTIVE_BLOCKED",
            "category": verdict["category"],
            "reason": verdict["reason"],
            "hint": "safety floor against accidental destruction — scope the path, "
                    "rephrase, or run it yourself if intended "
                    "(KORGEX_COMMAND_GUARD=off, or BYPASS policy, disables this).",
        }
        rec = LedgerIntent(
            "command_guard.block",
            {"tool": "Bash", "command": command[:200], "category": verdict["category"]},
            {"verdict": "DESTRUCTIVE_BLOCKED", "category": verdict["category"],
             "reason": verdict["reason"], "matched": verdict["matched"],
             "severity": verdict["severity"]},
            False)
        return GateOutcome(blocked=True, block_result=result, record=rec)


GATES: tuple[Gate, ...] = (WorkspaceGate(), GuardrailGate(), CommandGuardGate())  # populated by later tasks, in safety order


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
