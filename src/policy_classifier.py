"""Classifier permission mode — the 'auto' policy tier.

The deterministic edit policy (ask/workspace/session) can't express fuzzy human
intent like "let the agent edit migrations but never touch billing code" or
"installs are fine only if I asked for one." This mode lets the user write
natural-language rules in four buckets and has a cheap model judge each proposed
action against them:

  environment — context about the setup (NOT a rule; informs the judge)
  allow       — auto-approve actions matching these
  soft_deny   — block UNLESS the user's stated intent clearly authorizes it
                (this is the "preemptive block, unless clearly intended" behaviour)
  hard_deny   — block unconditionally; a security floor the model can't override

The judge returns ``{bucket, reason, intent_authorizes?}``; `resolve_verdict`
turns that into a gate ``(proceed, action, reason)``. The hard-block floor in
edit_policy still sits UNDER this — a classifier can never re-allow a protected
path. Everything here is pure except `decide_action`, which takes the model call
as an injected `judge` so it's testable with no network and fails safe.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

VALID_BUCKETS = ("environment", "allow", "soft_deny", "hard_deny")


@dataclass
class Rules:
    """User-authored permission rules, sorted into the four buckets."""
    environment: list = field(default_factory=list)
    allow: list = field(default_factory=list)
    soft_deny: list = field(default_factory=list)
    hard_deny: list = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.allow or self.soft_deny or self.hard_deny)


def parse_rules(raw: dict) -> Rules:
    """Build a Rules from a dict of bucket → list[str]. Missing buckets default to
    empty; non-list values are coerced to a single-item list; unknown keys ignored."""
    def lst(key):
        v = (raw or {}).get(key)
        if v is None:
            return []
        return list(v) if isinstance(v, (list, tuple)) else [str(v)]
    return Rules(environment=lst("environment"), allow=lst("allow"),
                 soft_deny=lst("soft_deny"), hard_deny=lst("hard_deny"))


def resolve_verdict(verdict: dict) -> tuple[bool, str, str]:
    """Turn a judge verdict into a gate decision ``(proceed, action, reason)``.

    allow → proceed. hard_deny → block. soft_deny → block unless
    ``intent_authorizes`` is true. Anything else (missing/garbage bucket) fails
    safe to ask — never a silent allow.
    """
    bucket = (verdict or {}).get("bucket")
    reason = (verdict or {}).get("reason", "")
    if bucket == "allow":
        return (True, "auto-allow", reason)
    if bucket == "hard_deny":
        return (False, "auto-block", reason or "hard-denied by policy")
    if bucket == "soft_deny":
        if verdict.get("intent_authorizes"):
            return (True, "auto-allow-intent", reason or "authorized by your stated intent")
        return (False, "auto-block-soft", reason or "soft-denied; your task didn't clearly ask for this")
    # environment is not an action verdict; treat like any unknown → ask.
    return (False, "auto-ask", reason or "unclassified — confirm")


def build_judge_prompt(action_desc: str, intent: str, rules: Rules) -> str:
    """The instruction handed to the cheap model. Returned (not sent) so callers
    can route it through their own model client."""
    def block(title, items):
        return f"{title}:\n" + ("\n".join(f"- {r}" for r in items) if items else "- (none)")
    return (
        "You are a permission classifier for an autonomous coding agent. Decide how to "
        "handle the PROPOSED ACTION given the user's rules and stated intent.\n\n"
        f"{block('ENVIRONMENT (context, not rules)', rules.environment)}\n\n"
        f"{block('ALLOW (auto-approve)', rules.allow)}\n\n"
        f"{block('SOFT_DENY (block UNLESS the stated intent clearly authorizes it)', rules.soft_deny)}\n\n"
        f"{block('HARD_DENY (block unconditionally)', rules.hard_deny)}\n\n"
        f"USER'S STATED INTENT: {intent or '(none given)'}\n"
        f"PROPOSED ACTION: {action_desc}\n\n"
        'Reply with ONLY a JSON object: {"bucket": "allow|soft_deny|hard_deny", '
        '"intent_authorizes": true|false, "reason": "<one short line>"}. '
        "Use hard_deny for anything matching a HARD_DENY rule or an obvious security/"
        "destructive boundary. Use soft_deny for SOFT_DENY matches and set "
        "intent_authorizes true only if the stated intent plainly calls for this action."
    )


def parse_judge_reply(text: str) -> dict:
    """Extract the verdict JSON from a model reply. Garbage → {} (→ fail-safe ask)."""
    if not text:
        return {}
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        obj = json.loads(text[start:end])
        return obj if isinstance(obj, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def decide_action(action_desc: str, *, intent: str, rules: Rules, env: list,
                  judge) -> tuple[bool, str, str]:
    """Classify one proposed action and resolve it to ``(proceed, action, reason)``.

    `judge(action_desc, intent, rules, env) -> verdict_dict` is injected (the real
    one calls a cheap model). Any judge error fails safe to ask — a broken or
    unreachable classifier must never auto-allow.
    """
    try:
        verdict = judge(action_desc, intent, rules, env)
    except Exception as e:  # model down / timeout / bad reply → never auto-allow
        return (False, "auto-ask", f"classifier unavailable ({type(e).__name__}); confirm")
    return resolve_verdict(verdict or {})
