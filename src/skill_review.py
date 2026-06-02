"""Self-learning skills — turn a finished turn into a reusable skill.

After a turn, ``review_turn`` asks a reviewer (the agent's own LLM, behind an
injectable interface so this all tests offline) one question: did this turn produce
a *reusable, generalizable* skill worth keeping? If so, ``apply_verdict`` writes (or
updates) an AGENT-created ``SKILL.md`` — and refuses to overwrite a user / built-in
skill of the same name, so the agent only ever edits its own.

Kept separate from the run loop so it can be invoked from a background thread.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


@dataclass
class SkillVerdict:
    action: str = "none"   # none | create | update
    name: str = ""
    description: str = ""
    body: str = ""
    reason: str = ""


def review_turn(user_msg: str, summary: str, existing_names, reviewer) -> SkillVerdict:
    """Ask `reviewer(context)->dict` whether to save a skill. Defaults to a no-op
    verdict on anything malformed — learning must never be forced or crash a turn."""
    ctx = {"user": user_msg, "summary": summary, "existing": list(existing_names or [])}
    try:
        raw = reviewer(ctx) or {}
    except Exception:
        return SkillVerdict("none", reason="reviewer error")
    action = (raw.get("action") or "none").lower()
    if action not in ("create", "update") or not raw.get("name") or not raw.get("body"):
        return SkillVerdict("none", reason=raw.get("reason", ""))
    return SkillVerdict(action=action, name=raw["name"], description=raw.get("description", ""),
                        body=raw["body"], reason=raw.get("reason", ""))


_REVIEW_SYSTEM = (
    "You are a skill curator for a coding agent. Given a just-finished turn, decide "
    "whether it produced a REUSABLE, GENERALIZABLE workflow worth saving as a skill "
    "for future tasks — a durable, repeatable procedure, NOT a one-off answer or "
    "project trivia. Most turns produce nothing saveable: prefer action 'none'. Only "
    "if there's a genuinely reusable procedure, return 'create' (or 'update' if it "
    "refines an existing skill, matched by name). Reply with ONLY a JSON object: "
    '{"action":"none|create|update","name":"kebab-case-name","description":"one line",'
    '"body":"the step-by-step procedure in markdown","reason":"why"}.'
)


def build_review_prompt(ctx: dict) -> str:
    existing = ", ".join(ctx.get("existing") or []) or "(none yet)"
    return (f"Existing skills: {existing}\n\n"
            f"User asked:\n{ctx.get('user', '')}\n\n"
            f"What happened / how it was solved:\n{ctx.get('summary', '')}\n\n"
            "Is there a reusable skill to save? Reply with the JSON object only.")


def parse_review_reply(text: str) -> dict:
    """Pull the first JSON object out of a model reply, tolerating prose/code fences."""
    import json as _json
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        out = _json.loads(m.group(0))
        return out if isinstance(out, dict) else {}
    except (ValueError, TypeError):
        return {}


def make_reviewer(complete):
    """Build a reviewer(ctx)->dict from a `complete(system, user) -> str` callable
    (the agent's one-off LLM call). Decoupled from the agent so it tests offline."""
    def reviewer(ctx):
        return parse_review_reply(complete(_REVIEW_SYSTEM, build_review_prompt(ctx)))
    return reviewer


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "skill"


def write_agent_skill(skills_dir: str, name: str, description: str, body: str) -> str:
    """Write ``skills_dir/<slug>/SKILL.md`` with ``trust: agent``. Returns the path."""
    d = os.path.join(skills_dir, _slug(name))
    os.makedirs(d, exist_ok=True)
    md = (f"---\nname: {name}\ndescription: {description}\nversion: 1.0\ntrust: agent\n---\n\n"
          f"{body.rstrip()}\n")
    path = os.path.join(d, "SKILL.md")
    with open(path, "w") as f:
        f.write(md)
    return path


def apply_verdict(verdict: SkillVerdict, skills_dir: str, *, registry=None) -> dict:
    """Persist a create/update verdict as an agent skill. Refuses to clobber a
    non-agent skill of the same name (provenance guard)."""
    if verdict.action not in ("create", "update") or not verdict.name or not verdict.body:
        return {"saved": False, "reason": "no-op"}
    if registry is not None:
        existing = registry.get(verdict.name)
        if existing is not None and getattr(existing, "trust", "agent") != "agent":
            return {"saved": False,
                    "reason": f"refused: '{verdict.name}' is a {existing.trust} skill, not agent-owned"}
    path = write_agent_skill(skills_dir, verdict.name, verdict.description, verdict.body)
    return {"saved": True, "path": path, "action": verdict.action, "name": verdict.name}
