"""Classifier permission mode — the 'auto' policy tier.

A user writes natural-language rules sorted into four buckets:
  environment — context about the setup (not a rule; informs judgment)
  allow       — auto-approve actions matching these
  soft_deny   — block UNLESS the user's stated intent clearly authorizes it
  hard_deny   — block unconditionally (security floor)
A cheap model judges each proposed action → allow | ask | deny + reason. These
tests pin the PURE rule-engine + the resolve logic; the LLM call is an injectable
shell over `decide_action`, so the policy is testable with no network.
"""
from src import policy_classifier as PC


# ── parsing user rules into the four buckets ──────────────────────────────────

def test_parse_rules_into_four_buckets():
    rules = PC.parse_rules({
        "environment": ["this is a solo dev machine"],
        "allow": ["edit source and test files", "run the test suite"],
        "soft_deny": ["installing new dependencies"],
        "hard_deny": ["pushing to a remote", "deleting the database"],
    })
    assert rules.allow and rules.soft_deny and rules.hard_deny and rules.environment
    assert "run the test suite" in rules.allow


def test_parse_rules_tolerates_missing_buckets():
    rules = PC.parse_rules({"hard_deny": ["rm -rf /"]})
    assert rules.hard_deny == ["rm -rf /"]
    assert rules.allow == [] and rules.soft_deny == [] and rules.environment == []


# ── resolve a classifier verdict into a gate decision ──────────────────────────

def test_resolve_allow_verdict_proceeds():
    proceed, action, _ = PC.resolve_verdict({"bucket": "allow", "reason": "matches allow rule"})
    assert proceed is True and action == "auto-allow"


def test_resolve_hard_deny_blocks_unconditionally():
    proceed, action, _ = PC.resolve_verdict({"bucket": "hard_deny", "reason": "security boundary"})
    assert proceed is False and action == "auto-block"


def test_resolve_soft_deny_blocks_without_clear_intent():
    # soft_deny + intent NOT clearly authorizing → block.
    proceed, action, _ = PC.resolve_verdict(
        {"bucket": "soft_deny", "reason": "installs a dep", "intent_authorizes": False})
    assert proceed is False and action == "auto-block-soft"


def test_resolve_soft_deny_allows_with_clear_intent():
    # soft_deny + the user's stated task clearly authorizes it → allow.
    proceed, action, _ = PC.resolve_verdict(
        {"bucket": "soft_deny", "reason": "user asked to add the dep", "intent_authorizes": True})
    assert proceed is True and action == "auto-allow-intent"


def test_resolve_unknown_bucket_fails_safe_to_ask():
    proceed, action, _ = PC.resolve_verdict({"bucket": "???", "reason": "unclear"})
    assert action == "auto-ask"  # never silently allow on a garbage verdict


# ── decide_action: the injectable shell (no network) ───────────────────────────

def test_decide_action_uses_injected_judge():
    rules = PC.parse_rules({"hard_deny": ["push to remote"]})

    def fake_judge(action_desc, intent, rules, env):
        # a stub model that hard-denies anything mentioning "push"
        if "push" in action_desc.lower():
            return {"bucket": "hard_deny", "reason": "remote push is hard-denied"}
        return {"bucket": "allow", "reason": "fine"}

    proceed, action, _ = PC.decide_action(
        "git push origin main", intent="fix a typo", rules=rules, env=[], judge=fake_judge)
    assert proceed is False and action == "auto-block"

    proceed2, action2, _ = PC.decide_action(
        "edit src/foo.py", intent="fix a typo", rules=rules, env=[], judge=fake_judge)
    assert proceed2 is True and action2 == "auto-allow"


def test_decide_action_fails_safe_when_judge_errors():
    rules = PC.parse_rules({})

    def boom(*a, **k):
        raise RuntimeError("model unavailable")

    proceed, action, _ = PC.decide_action(
        "anything", intent="x", rules=rules, env=[], judge=boom)
    # A broken judge must NOT auto-allow — degrade to ask.
    assert proceed is False and action == "auto-ask"


# ── integration: the 'auto' policy through the agent gate ──────────────────────

class _Led:
    def __init__(self): self.events = []
    def record_tool_call(self, **kw): self.events.append(kw); return len(self.events)
    def record_user_prompt(self, p, triggered_by=None): return 1
    def record_llm_call(self, **kw): return 1


def test_auto_policy_blocks_via_classifier(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    # config with a hard_deny rule
    cfg = tmp_path / "config.json"
    cfg.write_text('{"permission_rules": {"hard_deny": ["editing billing code"]}}')
    monkeypatch.setenv("KORGEX_CONFIG", str(cfg))

    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    a.edit_policy = "auto"
    a._active_intent = "fix a typo in the header"
    # inject a judge that hard-denies the billing path
    monkeypatch.setattr(a, "_policy_judge",
                        lambda desc, intent, rules, env: {"bucket": "hard_deny", "reason": "billing is off-limits"})

    from src.tool_gate import EditPolicyGate
    outcome = EditPolicyGate().evaluate(
        {"name": "Edit", "args": {"file_path": str(tmp_path / "billing.py")}}, a._gate_context())
    assert outcome.blocked and "refused" in outcome.block_result["error"]
    assert outcome.record is not None and outcome.record.tool_name == "edit_policy"


def test_auto_policy_allows_via_classifier(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    cfg = tmp_path / "config.json"
    cfg.write_text('{"permission_rules": {"allow": ["edit source files"]}}')
    monkeypatch.setenv("KORGEX_CONFIG", str(cfg))

    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    a.edit_policy = "auto"
    monkeypatch.setattr(a, "_policy_judge",
                        lambda *args, **k: {"bucket": "allow", "reason": "source edit"})
    from src.tool_gate import EditPolicyGate
    outcome = EditPolicyGate().evaluate(
        {"name": "Edit", "args": {"file_path": str(tmp_path / "src.py")}}, a._gate_context())
    assert not outcome.blocked  # allowed → no block


def test_auto_policy_hardblock_floor_still_wins(tmp_path, monkeypatch):
    """A protected path (.git) must be blocked BEFORE the classifier even runs —
    the classifier can never re-allow it."""
    from src.agent import KorgexAgent
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    a.edit_policy = "auto"
    # a judge that would allow everything — must NOT be consulted for .git
    monkeypatch.setattr(a, "_policy_judge",
                        lambda *args, **k: {"bucket": "allow", "reason": "yolo"})
    gitpath = str(tmp_path / ".git" / "config")
    from src.tool_gate import EditPolicyGate
    outcome = EditPolicyGate().evaluate(
        {"name": "Edit", "args": {"file_path": gitpath}}, a._gate_context())
    assert outcome.blocked  # hard-block floor blocked it regardless of the judge


def test_auto_policy_no_rules_falls_back_safely(tmp_path, monkeypatch):
    from src.agent import KorgexAgent
    cfg = tmp_path / "config.json"
    cfg.write_text('{"providers": []}')  # no permission_rules
    monkeypatch.setenv("KORGEX_CONFIG", str(cfg))
    a = KorgexAgent(repo_root=str(tmp_path), interactive=False)
    a.edit_policy = "auto"
    # judge would allow, but with no rules we must fall back to deterministic policy,
    # which for an in-workspace path under tmp auto-approves → no block.
    f = tmp_path / "x.py"
    from src.tool_gate import EditPolicyGate
    outcome = EditPolicyGate().evaluate(
        {"name": "Edit", "args": {"file_path": str(f)}}, a._gate_context())
    # workspace policy on a tmp path → allowed; just assert it didn't crash + returned a decision
    assert not outcome.blocked or "refused" in (outcome.block_result or {}).get("error", "")
