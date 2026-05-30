"""Edit-approval policy for korgex's file-mutating tools.

Three client-visible modes (ASK / WORKSPACE / SESSION), always-ask sensitive
paths, hard-blocked paths that are never editable, and a fail-safe default-DENY
on any prompt timeout/error. Pure + fast; the agent-loop wiring (checkpoint-
before-mutation + a ledger event per decision) layers on top of this core.
"""
from __future__ import annotations

from src.edit_policy import (
    ASK,
    SESSION,
    WORKSPACE,
    EditDecision,
    evaluate_edit,
    guard_decision,
    mutating_path,
    prompt_outcome_allows,
)


def test_hard_blocked_paths_are_never_editable_even_in_session_mode():
    for p in [".git/config", "repo/.git/HEAD", "/home/u/.ssh/id_rsa", "/home/u/.gnupg/secring"]:
        d = evaluate_edit(p, policy=SESSION, cwd="/home/u")
        assert isinstance(d, EditDecision) and d.action == "block", (p, d)


def test_sensitive_files_always_ask_even_when_session_would_auto_allow():
    for p in ["/x/.env", "/x/config/.env.local", "/x/deploy.pem", "/x/api.key",
              "/x/credentials.json", "/x/.npmrc", "/x/id_ed25519"]:
        assert evaluate_edit(p, policy=SESSION, cwd="/x").action == "ask", p


def test_ask_policy_prompts_for_ordinary_files():
    assert evaluate_edit("/repo/src/main.py", policy=ASK, cwd="/repo").action == "ask"


def test_session_policy_auto_allows_ordinary_files():
    assert evaluate_edit("/repo/src/main.py", policy=SESSION, cwd="/repo").action == "allow"


def test_workspace_policy_allows_inside_cwd_and_tmp_but_asks_outside():
    assert evaluate_edit("/repo/src/main.py", policy=WORKSPACE, cwd="/repo").action == "allow"
    assert evaluate_edit("src/main.py", policy=WORKSPACE, cwd="/repo").action == "allow"  # relative → under cwd
    assert evaluate_edit("/tmp/scratch.py", policy=WORKSPACE, cwd="/repo").action == "allow"
    assert evaluate_edit("/etc/hosts", policy=WORKSPACE, cwd="/repo").action == "ask"


def test_prompt_outcome_is_fail_safe_default_deny():
    assert prompt_outcome_allows("allow_once") is True
    assert prompt_outcome_allows("allow") is True
    for bad in ["deny", "timeout", "error", None, "", "maybe"]:
        assert prompt_outcome_allows(bad) is False, bad


def test_unknown_policy_never_silently_allows():
    assert evaluate_edit("/repo/x.py", policy="bogus", cwd="/repo").action in ("ask", "block")


def test_decision_carries_a_human_reason():
    d = evaluate_edit("/x/.env", policy=SESSION, cwd="/x")
    assert d.action == "ask" and d.reason and isinstance(d.reason, str)


def test_mutating_path_extracts_path_for_file_tools_only():
    assert mutating_path("Write", {"file_path": "/x/a.py"}) == "/x/a.py"
    assert mutating_path("Edit", {"file_path": "/x/a.py"}) == "/x/a.py"
    assert mutating_path("Bash", {"command": "rm x"}) is None  # not a file tool
    assert mutating_path("Read", {"file_path": "/x/a.py"}) is None
    assert mutating_path("Write", {}) is None


def test_guard_blocks_hard_blocked_regardless_of_policy():
    proceed, action, _ = guard_decision("/r/.git/config", policy=SESSION, cwd="/r", interactive=False)
    assert proceed is False and action == "block"


def test_guard_allows_ordinary_workspace_file():
    proceed, action, _ = guard_decision("/r/src/a.py", policy=WORKSPACE, cwd="/r", interactive=False)
    assert proceed is True and action == "allow"


def test_guard_headless_blocks_sensitive_but_proceeds_outside_workspace():
    # .env is sensitive → ASK; headless (no confirmer) must BLOCK it.
    p1, a1, _ = guard_decision("/r/.env", policy=SESSION, cwd="/r", interactive=False)
    assert p1 is False and "sensitive" in a1
    # ordinary file outside the workspace → ASK; headless proceeds-and-records.
    p2, a2, _ = guard_decision("/etc/hosts", policy=WORKSPACE, cwd="/r", interactive=False)
    assert p2 is True and "proceed" in a2


def test_guard_uses_confirmer_when_interactive():
    yes = guard_decision("/r/x.py", policy=ASK, cwd="/r", interactive=True, confirmer=lambda p: True)
    no = guard_decision("/r/x.py", policy=ASK, cwd="/r", interactive=True, confirmer=lambda p: False)
    assert yes[0] is True and yes[1] == "ask-approved"
    assert no[0] is False and no[1] == "ask-denied"
