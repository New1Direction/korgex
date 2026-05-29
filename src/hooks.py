"""
hooks.py — deterministic, event-driven, *ledger-native* extensibility (roadmap P1).

A hook is a shell command the harness (not the model) runs at a lifecycle event.
The cross-harness contract is standardized: the hook gets a JSON event on stdin
and may emit JSON on stdout; a PreToolUse hook can block the call.

What makes korgex's hooks differentiated rather than a Claude-Code clone: the
agent loop records every PreToolUse allow/deny as a *verdict event on the causal
ledger* (carrying a policy_hash of the rule that fired). Governance over tool
calls therefore becomes rewindable and auditable — something fire-and-forget
hooks in other harnesses cannot offer.

Config lives in `<repo_root>/.korgex/settings.json`:

    {
      "hooks": {
        "PreToolUse":  [{"matcher": "Bash", "command": "./scripts/guard.sh"}],
        "PostToolUse": [{"matcher": "Edit|Write", "command": "ruff format"}],
        "UserPromptSubmit": [{"command": "..."}],
        "Stop": [{"command": "..."}]
      }
    }

Decision protocol for a hook process:
  - exit 0                         → allow
  - exit 2                         → block (reason = stderr)
  - stdout {"decision":"block"}    → block (reason = "reason" field)
  - stdout {"additionalContext":…} → inject context (UserPromptSubmit)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lifecycle events korgex currently exposes seams for.
EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

DEFAULT_TIMEOUT_SECS = 30


def load_hooks(repo_root: str) -> dict:
    """Load the `hooks` table from <repo_root>/.korgex/settings.json.

    Missing file or malformed JSON → {} (hooks are opt-in; never crash startup).
    """
    path = Path(repo_root) / ".korgex" / "settings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("[hooks] ignoring malformed %s: %s", path, exc)
        return {}
    hooks = data.get("hooks", {})
    return hooks if isinstance(hooks, dict) else {}


def match_hooks(event_hooks: list, tool_name: str) -> list:
    """Return the hook defs whose `matcher` regex matches tool_name.

    An absent/empty matcher matches everything (useful for UserPromptSubmit/Stop
    where there is no tool name).
    """
    matched = []
    for hook in event_hooks or []:
        matcher = hook.get("matcher")
        if not matcher or re.search(matcher, tool_name or ""):
            matched.append(hook)
    return matched


def run_hook(command: str, payload: dict, timeout: float = DEFAULT_TIMEOUT_SECS,
             cwd: str = None) -> dict:
    """Run one hook command, piping `payload` as JSON on stdin.

    Returns {decision, reason, additional_context, exit_code, stdout, stderr}.
    Never raises — a crashing/timing-out hook degrades to allow (with a logged
    warning) so a broken hook can't wedge the agent.
    """
    try:
        proc = subprocess.run(
            command, shell=True, input=json.dumps(payload),
            capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        logger.warning("[hooks] command timed out after %ss: %s", timeout, command)
        return _result("allow", "", None, -1, "", "timeout")
    except Exception as exc:  # missing interpreter, etc.
        logger.warning("[hooks] command failed to run (%s): %s", type(exc).__name__, command)
        return _result("allow", "", None, -1, "", str(exc))

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    decision = "allow"
    reason = ""
    additional_context = None

    # Structured stdout takes precedence over exit code.
    if stdout:
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            d = str(data.get("decision", "")).lower()
            if d == "block":
                decision = "block"
                reason = str(data.get("reason", "") or "blocked by hook")
            additional_context = data.get("additionalContext")

    if decision != "block" and proc.returncode == 2:
        decision = "block"
        reason = stderr or "blocked by hook (exit 2)"

    return _result(decision, reason, additional_context, proc.returncode, stdout, stderr)


def run_event(event: str, tool_name: str, payload: dict, hooks: dict,
              cwd: str = None, timeout: float = DEFAULT_TIMEOUT_SECS) -> dict:
    """Run every hook matching `event`/`tool_name`. Aggregate the outcome.

    PreToolUse: first block wins. Other events are advisory (decision stays
    allow) but still collect injected context. `policy_hash` identifies the set
    of rules that fired, so a recorded verdict is attributable.
    """
    matched = match_hooks(hooks.get(event, []), tool_name)
    ran = []
    decision = "allow"
    reason = ""
    contexts = []

    for hook in matched:
        res = run_hook(hook.get("command", ""), payload,
                       timeout=hook.get("timeout", timeout), cwd=cwd)
        ran.append({"command": hook.get("command", ""), **res})
        if res.get("additional_context"):
            contexts.append(str(res["additional_context"]))
        if res["decision"] == "block" and decision != "block":
            decision = "block"
            reason = res["reason"]

    return {
        "decision": decision,
        "reason": reason,
        "additional_context": "\n".join(contexts) if contexts else None,
        "ran": ran,
        "policy_hash": _policy_hash(matched) if matched else "",
    }


# ── internals ─────────────────────────────────────────────────────────────

def _result(decision, reason, additional_context, exit_code, stdout, stderr) -> dict:
    return {
        "decision": decision,
        "reason": reason,
        "additional_context": additional_context,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }


def _policy_hash(matched: list) -> str:
    """SHA-256 over the canonical matched-rule set — the policy that produced a verdict."""
    canon = json.dumps(matched, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()
