"""Skill usage + lifecycle sidecar (self-learning skills, data layer).

A small JSON store next to the skills dir (``.korgex/skills/.usage.json``) tracking
per skill: when it was created, when it was last used, how many times, and a
lifecycle ``state`` — ``active → stale → archived`` (we NEVER delete a skill).

The sweep ages out only AGENT-created skills (``trust == "agent"``); user / built-in
/ installed skills are never touched. A used skill revives to ``active``. Timestamps
are epoch seconds passed in by the caller, so the logic is deterministic + testable.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

DAY = 86400.0
STALE_DAYS = 30
ARCHIVE_DAYS = 90


@dataclass
class UsageRecord:
    name: str
    created_at: float = 0.0
    last_used_at: float = 0.0
    use_count: int = 0
    state: str = "active"   # active | stale | archived


def lifecycle_state(last_used_at: float, now: float, *, stale_days: int = STALE_DAYS,
                    archive_days: int = ARCHIVE_DAYS) -> str:
    """The lifecycle state implied by how long a skill has been idle."""
    idle_days = (now - last_used_at) / DAY
    if idle_days >= archive_days:
        return "archived"
    if idle_days >= stale_days:
        return "stale"
    return "active"


class UsageStore:
    """JSON-backed map of skill name → UsageRecord."""

    def __init__(self, path: str):
        self.path = path
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path) as f:
                raw = json.load(f)
            self._data = {k: UsageRecord(**v) for k, v in raw.items()}
        except (FileNotFoundError, ValueError, OSError, TypeError):
            self._data = {}

    def _save(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({k: asdict(v) for k, v in self._data.items()}, f, indent=2)
        os.replace(tmp, self.path)  # atomic

    def get(self, name: str):
        return self._data.get(name)

    def all(self) -> list:
        return list(self._data.values())

    def ensure(self, name: str, now: float) -> UsageRecord:
        rec = self._data.get(name)
        if rec is None:
            rec = UsageRecord(name=name, created_at=now, last_used_at=now)
            self._data[name] = rec
            self._save()
        return rec

    def record_use(self, name: str, now: float) -> UsageRecord:
        rec = self.ensure(name, now)
        rec.use_count += 1
        rec.last_used_at = now
        rec.state = "active"   # using a skill revives it
        self._save()
        return rec

    def set_state(self, name: str, state: str) -> None:
        rec = self._data.get(name)
        if rec is not None and rec.state != state:
            rec.state = state
            self._save()


def global_skills_dir() -> str:
    """User-global skills dir (~/.korgex/skills) — where learned (agent) skills and
    the shared usage sidecar live, so the flywheel accumulates across projects."""
    return os.path.join(os.path.expanduser("~"), ".korgex", "skills")


def usage_path(skills_dir: str) -> str:
    """The usage sidecar path for a skills directory."""
    return os.path.join(skills_dir, ".usage.json")


def record_use(skills_dir: str, name: str, now: float) -> UsageRecord:
    """Convenience: record one use of `name` in the sidecar under `skills_dir`."""
    return UsageStore(usage_path(skills_dir)).record_use(name, now)


def overview(registry, store: UsageStore) -> list:
    """One row per registered skill, merged with usage: {name, trust, state, uses}."""
    rows = []
    for name in registry.names():
        sk = registry.get(name)
        rec = store.get(name)
        rows.append({
            "name": name,
            "trust": getattr(sk, "trust", "user"),
            "state": rec.state if rec else "active",
            "uses": rec.use_count if rec else 0,
        })
    return rows


def sweep(store: UsageStore, registry, now: float, *, stale_days: int = STALE_DAYS,
          archive_days: int = ARCHIVE_DAYS) -> list:
    """Age agent-created skills by idle time. Returns [(name, old_state, new_state)].
    Provenance filter: only skills the registry reports as ``trust == "agent"`` are
    eligible — user / built-in / installed skills are never re-stated."""
    transitions = []
    for rec in store.all():
        sk = registry.get(rec.name) if registry is not None else None
        if sk is None or getattr(sk, "trust", None) != "agent":
            continue
        new = lifecycle_state(rec.last_used_at, now, stale_days=stale_days, archive_days=archive_days)
        if new != rec.state:
            old = rec.state
            store.set_state(rec.name, new)
            transitions.append((rec.name, old, new))
    return transitions
