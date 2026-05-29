"""
korgantic mode tests — the effort dial + workflow chaining + quality patterns.

6 effort levels (auto → low → medium → high → xhigh → ultracode). Higher levels
chain phases (understand → design → implement → review) and apply quality
patterns: adversarial verify, multi-modal sweep, completeness critic,
loop-until-dry. ultracode = "token cost is not a constraint" (unbounded budget).

The controller and patterns are pure orchestration over an INJECTED runner, so
they're fully testable without any live LLM call.
"""

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import korgantic as K  # noqa: E402


# ── 1. effort levels + profiles ───────────────────────────────────────────

def test_six_ordered_levels():
    assert K.EFFORT_LEVELS == ["auto", "low", "medium", "high", "xhigh", "ultracode"]


def test_ultracode_budget_is_unbounded():
    _, prof = K.resolve_effort("ultracode", "anything")
    assert prof["token_budget"] is None  # "token cost is not a constraint"


def test_low_is_bounded_and_single_phase():
    _, prof = K.resolve_effort("low", "anything")
    assert prof["token_budget"] is not None
    assert prof["phases"] == ["implement"]
    assert prof["verifiers"] == 0


def test_effort_escalates_phase_count_and_verifiers():
    levels = ["low", "medium", "high", "xhigh", "ultracode"]
    phase_counts = [len(K.resolve_effort(l, "x")[1]["phases"]) for l in levels]
    verifiers = [K.resolve_effort(l, "x")[1]["verifiers"] for l in levels]
    assert phase_counts == sorted(phase_counts)        # non-decreasing
    assert verifiers == sorted(verifiers)              # non-decreasing
    assert phase_counts[-1] == 4                        # ultracode runs all 4 phases


def test_auto_resolves_to_concrete_level_by_task():
    assert K.resolve_effort("auto", "fix the typo in the README")[0] == "low"
    assert K.resolve_effort("auto", "comprehensively audit the entire auth system")[0] == "high"


# ── 2. quality patterns (pure, injected runner) ───────────────────────────

def test_adversarial_verify_confirms_when_quorum_not_refuted():
    runner = lambda role, prompt, output_schema=None: {
        "success": True, "result": {"refuted": False, "reason": "holds up"}}
    v = K.adversarial_verify("bug A is real", runner, n=3, quorum=2)
    assert v["confirmed"] is True
    assert v["votes"].count(False) == 3  # nobody refuted


def test_adversarial_verify_kills_when_majority_refute():
    calls = {"i": 0}

    def runner(role, prompt, output_schema=None):
        calls["i"] += 1
        # 2 of 3 refute → only 1 not-refuted < quorum 2 → killed
        return {"success": True, "result": {"refuted": calls["i"] <= 2}}

    v = K.adversarial_verify("dubious finding", runner, n=3, quorum=2)
    assert v["confirmed"] is False


def test_loop_until_dry_stops_after_consecutive_empty_rounds():
    script = [["a"], ["b"], [], [], ["c"]]  # would keep going, but 2 dry stops it
    it = iter(script)
    rounds = K.loop_until_dry(lambda: next(it), dry_threshold=2, max_rounds=10)
    assert rounds == [["a"], ["b"], [], []]  # stopped at the 2nd consecutive empty


def test_loop_until_dry_respects_max_rounds():
    rounds = K.loop_until_dry(lambda: ["never dry"], dry_threshold=2, max_rounds=3)
    assert len(rounds) == 3


def test_multi_modal_sweep_runs_one_agent_per_lens():
    seen = []
    runner = lambda role, prompt, output_schema=None: (seen.append(prompt) or
                                                       {"success": True, "result": {"summary": prompt}})
    out = K.multi_modal_sweep(["structure", "tests", "risks"], runner, "the codebase")
    assert len(out) == 3
    assert any("structure" in p for p in seen) and any("risks" in p for p in seen)


def test_completeness_critic_returns_missing_items():
    runner = lambda role, prompt, output_schema=None: {
        "success": True, "result": {"missing": ["no error handling", "untested edge case"]}}
    missing = K.completeness_critic("the task", runner)
    assert "no error handling" in missing


# ── 3. the controller: effort scales the orchestration ────────────────────

class _Runner:
    """Records every role invoked; returns role-appropriate canned results."""

    def __init__(self):
        self.roles = []

    def __call__(self, role, prompt, output_schema=None):
        self.roles.append(role)
        if role == "review":
            return {"success": True, "result": {"findings": [{"title": "off-by-one"}]}}
        if role == "verify":
            return {"success": True, "result": {"refuted": False, "reason": "reproduced"}}
        if role == "critic":
            return {"success": True, "result": {"missing": []}}
        if role == "implement":
            return {"success": True, "result": {"changes": []}}  # dry immediately
        return {"success": True, "result": {"summary": f"{role} done"}}


def test_low_effort_runs_only_implement():
    r = _Runner()
    out = K.run_korgantic("do a thing", "low", r)
    assert out["phases_run"] == ["implement"]
    assert set(r.roles) == {"implement"}
    assert "verify" not in r.roles and "review" not in r.roles


def test_ultracode_chains_all_phases_and_verifies_findings():
    r = _Runner()
    out = K.run_korgantic("build the thing", "ultracode", r)

    # full chain ran
    for phase in ("understand", "design", "implement", "review"):
        assert phase in out["phases_run"]
    # multi-modal sweep happened in understand (several lenses)
    assert r.roles.count("understand") >= 2
    # the review finding was adversarially verified (3 skeptics) and confirmed
    assert r.roles.count("verify") == 3
    assert out["findings"] and out["findings"][0]["verdict"]["confirmed"] is True
    # completeness critic ran
    assert "completeness" in out["phases_run"]
    # result reports the resolved level
    assert out["effort"] == "ultracode"


def test_medium_reviews_but_does_not_sweep_or_criticize():
    r = _Runner()
    out = K.run_korgantic("medium task", "medium", r)
    assert "implement" in out["phases_run"] and "review" in out["phases_run"]
    assert "understand" not in out["phases_run"]   # no sweep at medium
    assert "completeness" not in out["phases_run"]  # no critic at medium


# ── 4. production wiring on KorgexAgent ───────────────────────────────────

class _FakeLedger:
    def record_user_prompt(self, prompt, triggered_by=None):
        return 1

    def record_llm_call(self, **kw):
        return 2

    def record_tool_call(self, **kw):
        return None


def test_run_korgantic_task_routes_phases_through_chained_run_task():
    from src.agent import KorgexAgent

    class _A(KorgexAgent):
        def __init__(self, **kw):
            kw.setdefault("model", "gpt-4o")
            kw.setdefault("interactive", False)
            super().__init__(**kw)
            self.ledger = _FakeLedger()
            self.run_calls = []

        def run_task(self, prompt, output_schema=None, parent_seq=None, tools_filter=None):
            self.run_calls.append({"prompt": prompt, "parent_seq": parent_seq})
            return {"success": True, "result": {"changes": []}}

    a = _A()
    out = a.run_korgantic_task("fix a typo", effort="low")

    assert out["effort"] == "low"
    assert out["phases_run"] == ["implement"]
    # every phase ran as a run_task chained under the single korgantic root seq
    assert a.run_calls
    assert all(c["parent_seq"] == 1 for c in a.run_calls)
