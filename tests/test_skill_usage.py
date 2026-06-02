"""Skill usage + lifecycle sidecar — the data layer for self-learning skills.

Tracks, per skill, how often + how recently it was used and a lifecycle state
(active → stale → archived, never deleted). Used skills revive to active; the
sweep only ages out AGENT-created skills (provenance filter) — your own skills
are never touched. Timestamps are passed in (epoch secs) so it's deterministic.
"""
from src.skills import Skill, SkillRegistry
from src.skill_usage import UsageStore, lifecycle_state, sweep

DAY = 86400.0


def test_record_use_increments_count_and_marks_active(tmp_path):
    store = UsageStore(str(tmp_path / ".usage.json"))
    r = store.record_use("fix-flaky", now=1000.0)
    assert r.use_count == 1 and r.last_used_at == 1000.0 and r.state == "active"
    r2 = store.record_use("fix-flaky", now=2000.0)
    assert r2.use_count == 2 and r2.last_used_at == 2000.0


def test_get_unknown_is_none(tmp_path):
    assert UsageStore(str(tmp_path / ".usage.json")).get("nope") is None


def test_store_persists_across_instances(tmp_path):
    p = str(tmp_path / ".usage.json")
    UsageStore(p).record_use("a", now=10.0)
    assert UsageStore(p).get("a").use_count == 1


def test_lifecycle_state_ages_by_idle_time():
    now = 100 * DAY
    assert lifecycle_state(now - 1 * DAY, now) == "active"
    assert lifecycle_state(now - 40 * DAY, now) == "stale"     # > 30d idle
    assert lifecycle_state(now - 100 * DAY, now) == "archived"  # > 90d idle


def test_record_use_revives_a_stale_skill(tmp_path):
    store = UsageStore(str(tmp_path / ".usage.json"))
    store.record_use("a", now=0.0)
    store.set_state("a", "stale")
    revived = store.record_use("a", now=5.0)
    assert revived.state == "active"


def _registry(*pairs):
    # pairs: (name, trust)
    return SkillRegistry([Skill(name=n, description="d", body="b", trust=t) for n, t in pairs])


def test_record_use_free_fn_writes_to_skills_dir(tmp_path):
    from src.skill_usage import record_use, usage_path
    record_use(str(tmp_path), "a", now=5.0)
    store = UsageStore(usage_path(str(tmp_path)))
    assert store.get("a").use_count == 1


def test_overview_merges_registry_with_usage(tmp_path):
    from src.skill_usage import overview
    store = UsageStore(str(tmp_path / ".usage.json"))
    store.record_use("learned", now=0.0)
    reg = _registry(("learned", "agent"), ("mine", "user"))
    rows = {r["name"]: r for r in overview(reg, store)}
    assert rows["learned"]["trust"] == "agent" and rows["learned"]["uses"] == 1
    assert rows["mine"]["uses"] == 0 and rows["mine"]["state"] == "active"


def test_sweep_only_ages_agent_created_skills(tmp_path):
    store = UsageStore(str(tmp_path / ".usage.json"))
    now = 100 * DAY
    # both unused for 100 days, but only the agent one is in scope
    store.record_use("learned", now=now - 100 * DAY)
    store.record_use("mine", now=now - 100 * DAY)
    reg = _registry(("learned", "agent"), ("mine", "user"))

    transitions = sweep(store, reg, now=now)

    moved = {name: new for (name, _old, new) in transitions}
    assert moved == {"learned": "archived"}        # agent skill aged out
    assert store.get("mine").state == "active"      # user skill untouched
