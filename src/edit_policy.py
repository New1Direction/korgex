"""Edit-approval policy for file-mutating tools.

A small, pure decision layer the agent loop consults before any tool writes to
disk. Three client-visible modes, always-ask sensitive paths, hard-blocked paths
that are never editable, and a fail-safe default-DENY on prompt timeout/error.

    decision = evaluate_edit(path, policy=WORKSPACE, cwd=repo_root)
    if decision.action == "block":   refuse
    elif decision.action == "ask":   prompt; proceed only if prompt_outcome_allows(outcome)
    else:                            proceed (checkpoint first, then mutate)

Kept dependency-free and side-effect-free so it's trivially testable; the agent
wiring (checkpoint-before-mutation + a ledger event per decision) layers on top.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# ── policy modes (client-visible) ───────────────────────────────────────────
ASK = "ask"            # confirm every edit
WORKSPACE = "workspace"  # auto-approve inside the workspace/tmp; confirm outside
SESSION = "session"    # auto-approve for the whole session
AUTO = "auto"          # an LLM classifies each action vs the user's permission rules
                       # (allow/soft_deny/hard_deny buckets) — see policy_classifier.py.
                       # The hard-block floor (is_hard_blocked) always applies first.

# ── decision actions ────────────────────────────────────────────────────────
ALLOW = "allow"
PROMPT = "ask"
BLOCK = "block"

# Directories whose contents must never be edited by the agent.
HARD_BLOCK_DIRS = {".git", ".ssh", ".gnupg"}
# Directories that are always sensitive (ask) even if not hard-blocked.
SENSITIVE_DIRS = {".aws", ".kube", ".docker"}
SENSITIVE_BASENAMES = {".npmrc", ".pypirc", ".netrc", ".htpasswd", ".dockercfg"}
SENSITIVE_EXTS = {".pem", ".key", ".keystore", ".p12", ".pfx"}
_TMP_ROOTS = ("/tmp", "/var/tmp", "/private/tmp")


@dataclass(frozen=True)
class EditDecision:
    """The verdict for one proposed file mutation."""

    action: str   # ALLOW | PROMPT | BLOCK
    reason: str


def _components(path: str) -> list[str]:
    return os.path.normpath(path).split(os.sep)


def is_hard_blocked(file_path: str) -> bool:
    """True if the path lives under a protected directory (git/ssh/gnupg internals)."""
    return any(c in HARD_BLOCK_DIRS for c in _components(file_path))


def is_sensitive(file_path: str) -> bool:
    """True for credentials/keys/dotfiles that should ALWAYS be confirmed."""
    base = os.path.basename(file_path.rstrip("/")).lower()
    if not base:
        return False
    if base.startswith(".env"):        # .env, .env.local, .env.production
        return True
    if base.startswith("id_"):         # ssh private keys (id_rsa, id_ed25519)
        return True
    if base in SENSITIVE_BASENAMES:
        return True
    if os.path.splitext(base)[1] in SENSITIVE_EXTS:
        return True
    if "credential" in base or "secret" in base:
        return True
    return any(c in SENSITIVE_DIRS for c in _components(file_path))


def _under(path: str, root: str) -> bool:
    if not os.path.isabs(path):
        return True  # relative paths resolve under cwd by definition
    try:
        ap, ar = os.path.abspath(path), os.path.abspath(root)
        return os.path.commonpath([ap, ar]) == ar
    except ValueError:
        return False


def _in_tmp(path: str) -> bool:
    return os.path.isabs(path) and any(_under(path, r) for r in _TMP_ROOTS)


def evaluate_edit(file_path: str, *, policy: str = ASK, cwd: str | None = None) -> EditDecision:
    """Decide whether a proposed edit to `file_path` should allow / ask / block.

    Precedence: hard-block > sensitive (always ask) > policy mode. Any unknown
    policy falls through to ASK — it never silently allows.
    """
    cwd = cwd or os.getcwd()
    if is_hard_blocked(file_path):
        return EditDecision(BLOCK, f"protected location (.git/.ssh/.gnupg): {file_path}")
    if is_sensitive(file_path):
        return EditDecision(PROMPT, f"sensitive file — always confirm: {file_path}")
    if policy == SESSION:
        return EditDecision(ALLOW, "session policy: auto-approved")
    if policy == WORKSPACE:
        if _under(file_path, cwd) or _in_tmp(file_path):
            return EditDecision(ALLOW, "workspace policy: inside workspace/tmp")
        return EditDecision(PROMPT, f"workspace policy: outside the workspace — confirm: {file_path}")
    # ASK, or any unrecognized policy → fail safe to prompting.
    return EditDecision(PROMPT, "ask policy: confirm every edit")


def prompt_outcome_allows(outcome) -> bool:
    """Fail-safe: only an explicit affirmative proceeds. Timeout/error/deny/None → DENY."""
    return outcome in ("allow_once", "allow", "approve", "yes")


# ── agent-loop helpers ──────────────────────────────────────────────────────
MUTATING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_PATH_KEYS = ("file_path", "filepath", "path", "notebook_path")


def mutating_path(tool_name: str, args: dict) -> str | None:
    """The target path if `tool_name` is a file-mutating tool, else None."""
    if tool_name not in MUTATING_TOOLS or not isinstance(args, dict):
        return None
    for k in _PATH_KEYS:
        if args.get(k):
            return args[k]
    return None


def guard_decision(file_path: str, *, policy: str, cwd: str | None,
                   interactive: bool, confirmer=None) -> tuple[bool, str, str]:
    """Resolve a proposed edit to ``(proceed, action, reason)``.

    Applies the policy, then resolves an ASK: interactively via ``confirmer`` if
    one is bound, otherwise the headless fail-safe — **sensitive paths are
    blocked** (no human to vouch for them) while ordinary outside-workspace edits
    proceed-and-record (so automation isn't broken). Hard-blocked paths never
    proceed; nothing silently allows.
    """
    d = evaluate_edit(file_path, policy=policy, cwd=cwd)
    if d.action == BLOCK:
        return (False, "block", d.reason)
    if d.action == ALLOW:
        return (True, "allow", d.reason)
    # d.action == PROMPT
    if interactive and confirmer is not None:
        approved = bool(confirmer(file_path))
        return (approved, "ask-approved" if approved else "ask-denied", d.reason)
    if is_sensitive(file_path):
        return (False, "block-sensitive-headless", d.reason)
    return (True, "ask-proceed-headless", d.reason)
