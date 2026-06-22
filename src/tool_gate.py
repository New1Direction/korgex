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
from src import egress_guard as _egress
from src import plan_mode as _PM


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


class EgressGate:
    """Gate E: shape-based guard over data leaving the box. Records egress.flag
    (advisory, always allows), egress.redact (redacts, allows), or egress.block
    (blocks). OFF under BYPASS; fails open (any exception → ALLOW)."""
    name = "egress"

    def evaluate(self, call: dict, ctx: GateContext) -> GateOutcome:
        if ctx.edit_policy == _EP.BYPASS:
            return ALLOW
        try:
            mode = _egress.mode_from_env(os.environ)
            if mode == "off":
                return ALLOW
            name = call.get("name")
            args = call.get("args") or {}
            if not _egress.is_outbound(name, args, mcp_tools=ctx.mcp_tools):
                return ALLOW
            allow = _egress.split_env_list("KORGEX_EGRESS_ALLOW")
            deny = _egress.split_env_list("KORGEX_EGRESS_DENY")
            verdict = _egress.inspect(name, args, allow=allow, deny=deny,
                                      mcp_tools=ctx.mcp_tools)
            if not verdict["findings"] and not verdict["denied_by_list"]:
                return ALLOW
            new_args, action = _egress.apply(verdict, name, args, mode)
            event = {"allow": "egress.flag", "redacted": "egress.redact",
                     "blocked": "egress.block"}.get(action, "egress.flag")
            rec = LedgerIntent(
                event, {"tool": name, "destination": verdict.get("destination")},
                _egress.verdict_payload(name, verdict, mode=mode, action=action,
                                        allow=allow, deny=deny),
                action != "blocked")
            shapes = ", ".join(sorted({f["label"] for f in verdict["findings"]})) \
                or "denied destination"
            if action == "blocked":
                result = {
                    "error": f"blocked: egress guard — outbound payload contains {shapes} "
                             f"bound for {verdict.get('destination') or 'an external destination'}",
                    "verdict": "EGRESS_BLOCKED",
                    "hint": "a secret/exfil shape was detected leaving the box. Remove it, or "
                            "set KORGEX_EGRESS=flag (warn only) or off if this is intended.",
                }
                return GateOutcome(blocked=True, block_result=result, record=rec)
            if action == "redacted":
                return GateOutcome(new_args=new_args, record=rec)
            return GateOutcome(record=rec)  # flag: record, allow
        except Exception:
            return ALLOW  # fail-open


class PlanModeGate:
    """Gate P: plan-mode read-only enforcement. When plan_mode is active,
    blocks all side-effecting tools except Write to the plan file itself.
    Records plan_mode.block on block only."""
    name = "plan_mode"

    def evaluate(self, call: dict, ctx: GateContext) -> GateOutcome:
        if not ctx.plan_mode_active:
            return ALLOW
        block = _PM.is_blocked(call.get("name"), call.get("args") or {}, ctx.plan_path)
        if block is None:
            return ALLOW
        return GateOutcome(
            blocked=True, block_result=block,
            record=LedgerIntent(
                "plan_mode.block", {"tool": call.get("name")},
                {"verdict": "PLAN_MODE_READONLY", "reason": block["reason"]}, False))


class EditPolicyGate:
    """Gate E (edit): edit approval policy enforcement. Applies only to mutating
    tools (Write/Edit); otherwise ALLOW. Routes to ctx.classify_edit for
    policy=auto (unless hard-blocked), else ctx.guard_decision. Records
    edit_policy on allow AND block. Calls ctx.checkpoint only when proceeding."""
    name = "edit_policy"

    def evaluate(self, call: dict, ctx: GateContext) -> GateOutcome:
        args = call.get("args") or {}
        path = _EP.mutating_path(call.get("name"), args)
        if path is None:
            return ALLOW
        if ctx.edit_policy == _EP.AUTO and not _EP.is_hard_blocked(path):
            proceed, action, reason = ctx.classify_edit(call, path)
        else:
            proceed, action, reason = _EP.guard_decision(
                path, policy=ctx.edit_policy, cwd=ctx.repo_root,
                interactive=ctx.interactive, confirmer=ctx.confirmer)
        sha = ctx.checkpoint(path) if proceed else None
        rec = LedgerIntent(
            "edit_policy",
            {"tool": call.get("name"), "path": path, "policy": ctx.edit_policy},
            {"action": action, "reason": reason, "allowed": proceed, "checkpoint": sha},
            proceed)
        if not proceed:
            return GateOutcome(
                blocked=True,
                block_result={"error": "edit refused by approval policy",
                              "verdict": action.upper().replace("-", "_"), "reason": reason},
                record=rec)
        return GateOutcome(record=rec)


GATES: tuple[Gate, ...] = (WorkspaceGate(), GuardrailGate(), CommandGuardGate(), EgressGate(), PlanModeGate(), EditPolicyGate())  # safety order; extended by later tasks


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
