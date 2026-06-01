"""Self-learning skill CURATOR — keep the learned library clean.

``skill_review`` grows the library one turn at a time; over many sessions that
accumulates near-duplicates (three subtly-different "run the test suite" skills).
The curator is the consolidation pass: an LLM groups the AGENT-learned skills by
intent, then each group is merged into one skill and the redundant ones deleted.

The invariant, enforced on every write AND delete: the curator only ever touches
``trust: agent`` skills. User / built-in / installed skills are never read for
merging, never rewritten, never deleted. Mirrors ``skill_review`` so it tests
offline (the LLM is an injected ``complete``/``curator`` callable) and runs from a
background thread.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field


@dataclass
class CurationGroup:
    into: str                       # name of the consolidated skill
    members: list                   # agent-skill names folded in (incl. `into` if it exists)
    description: str = ""
    body: str = ""
    reason: str = ""


@dataclass
class CurationPlan:
    groups: list = field(default_factory=list)


def agent_skills(registry) -> list:
    """The curator's scope: only the skills korgex learned itself (trust: agent),
    sorted by name. Everything else is off-limits."""
    out = [registry.get(n) for n in registry.names()]
    return [s for s in out if s is not None and getattr(s, "trust", None) == "agent"]


_CURATE_SYSTEM = (
    "You are a librarian for a coding agent's self-learned skills. You are given the "
    "agent's current skills (name + description). Find groups that are REDUNDANT — "
    "same underlying procedure, just worded differently — and propose how to merge "
    "each group into ONE consolidated skill. Do NOT merge skills that do genuinely "
    "different things; when in doubt, leave them separate. Reply with ONLY a JSON "
    "array; each element is "
    '{"into":"kebab-name-to-keep","merge":["name1","name2",...],'
    '"description":"one line","body":"the merged step-by-step procedure in markdown",'
    '"reason":"why these are the same"}. Return [] if nothing should be merged.'
)


def build_curation_prompt(skills) -> str:
    if not skills:
        return "The agent has no learned skills. Reply with []."
    listing = "\n".join(f"- {s.name} — {s.description}" for s in skills)
    return (f"Current agent-learned skills:\n{listing}\n\n"
            "Which (if any) are redundant and should be merged? Reply with the JSON array only.")


def parse_curation_reply(text: str) -> list:
    """Pull the first JSON array out of a model reply, tolerating prose/code fences.
    Returns [] on anything malformed — curation must never be forced or crash."""
    import json as _json
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        out = _json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    return [g for g in out if isinstance(g, dict)] if isinstance(out, list) else []


def make_curator(complete):
    """Build a ``curator(ctx)->list`` from a ``complete(system, user) -> str`` callable
    (the agent's one-off LLM call). Decoupled from the agent so it tests offline."""
    def curator(ctx):
        skills = ctx.get("skills") or []
        listing = "\n".join(f"- {s['name']} — {s.get('description', '')}" for s in skills)
        user = (f"Current agent-learned skills:\n{listing}\n\n"
                "Which (if any) are redundant and should be merged? Reply with the JSON array only.")
        return parse_curation_reply(complete(_CURATE_SYSTEM, user))
    return curator


def plan_curation(registry, curator) -> CurationPlan:
    """Ask `curator(ctx)->list` for merge groups and VALIDATE them into a plan.

    A group survives only if: every member resolves to an *agent* skill (non-agent
    and unknown names are dropped), at least 2 agent members remain (else it isn't a
    merge), and it carries a body. `into` defaults to the first surviving member.
    Anything malformed yields an empty plan — never raises.
    """
    agents = agent_skills(registry)
    agent_names = {s.name for s in agents}
    ctx = {"skills": [{"name": s.name, "description": s.description} for s in agents]}
    try:
        raw = curator(ctx)
    except Exception:
        return CurationPlan()
    if not isinstance(raw, list):
        return CurationPlan()

    groups = []
    for g in raw:
        if not isinstance(g, dict):
            continue
        members = [m for m in (g.get("merge") or []) if m in agent_names]
        members = list(dict.fromkeys(members))           # de-dupe, keep order
        body = g.get("body") or ""
        if len(members) < 2 or not body:
            continue
        into = g.get("into") if g.get("into") in members else members[0]
        groups.append(CurationGroup(
            into=into, members=members, description=g.get("description", ""),
            body=body, reason=g.get("reason", "")))
    return CurationPlan(groups=groups)


def _under(path: str, root: str) -> bool:
    """True if `path` is inside `root` — a hard guard before any rmtree."""
    try:
        ap, ar = os.path.abspath(path), os.path.abspath(root)
        return ap == ar or ap.startswith(ar + os.sep)
    except Exception:
        return False


def apply_curation(plan: CurationPlan, skills_dir: str, registry) -> dict:
    """Persist a plan: write each consolidated (agent) skill, then delete the
    redundant source dirs. PROVENANCE GUARD: refuse to clobber a non-agent `into`
    name, and only ever delete a member that is an agent skill living under
    `skills_dir`. Returns {merged, removed, skipped}.
    """
    from src.skill_review import write_agent_skill

    merged, removed, skipped = [], [], []
    for g in plan.groups:
        into_existing = registry.get(g.into)
        if into_existing is not None and getattr(into_existing, "trust", "agent") != "agent":
            skipped.append(g.into)                       # never overwrite a user/built-in skill
            continue
        write_agent_skill(skills_dir, g.into, g.description, g.body)
        merged.append(g.into)
        for member in g.members:
            if member == g.into:
                continue
            sk = registry.get(member)
            if (sk is not None and getattr(sk, "trust", None) == "agent"
                    and sk.path and _under(sk.path, skills_dir)):
                shutil.rmtree(os.path.dirname(sk.path), ignore_errors=True)
                removed.append(member)
            else:
                skipped.append(member)                   # not agent-owned / outside dir → leave it
    return {"merged": merged, "removed": removed, "skipped": skipped}
