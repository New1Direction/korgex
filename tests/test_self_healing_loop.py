"""
Auto-heal-to-green loop (roadmap idea #8).

When the in-loop test gate goes red, korgex auto-spawns a healing subagent with
the failure log and retries — bounded — until red→green or attempts exhausted.
Every attempt and the final verdict is recorded as a ledger event, so (because
the ledger is hash-chained) a self-repair is itself a verifiable, replayable
trail — korgex's analog of thumper's recovery loop.

These tests pin the pure loop (`auto_heal_to_green`) with injected fakes: a gate
runner returning a scripted sequence, a heal_fn, and a recorder. The agent wiring
is tested separately.
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.self_healing import auto_heal_to_green  # noqa: E402


def _recorder():
    events, seq = [], [0]

    def record(tool_name, args, result, success, triggered_by):
        seq[0] += 1
        events.append({"seq": seq[0], "tool_name": tool_name, "args": args,
                       "result": result, "success": success, "triggered_by": triggered_by})
        return seq[0]

    return events, record


def test_heal_resolves_red_to_green():
    events, record = _recorder()
    state = {"fixed": False}
    gates = iter([{"passed": True, "exit_code": 0, "output": ""}])  # green after one heal
    final = auto_heal_to_green(
        {"passed": False, "exit_code": 1, "output": "FAILED: assert"},
        run_gate=lambda: next(gates),
        heal_fn=lambda output: state.__setitem__("fixed", True),
        record_event=record, max_attempts=3, triggered_by=10)
    assert final["passed"] is True
    assert state["fixed"]
    assert [e["tool_name"] for e in events] == ["heal.attempt", "heal.resolved"]
    assert events[0]["triggered_by"] == 10                 # first attempt ← the red gate
    assert events[1]["triggered_by"] == events[0]["seq"]   # resolved ← that attempt


def test_heal_exhausts_after_max_attempts():
    events, record = _recorder()
    calls = {"heal": 0}
    final = auto_heal_to_green(
        {"passed": False, "exit_code": 1, "output": "red"},
        run_gate=lambda: {"passed": False, "exit_code": 1, "output": "still red"},
        heal_fn=lambda output: calls.__setitem__("heal", calls["heal"] + 1),
        record_event=record, max_attempts=3, triggered_by=5)
    assert final["passed"] is False
    assert calls["heal"] == 3
    assert [e["tool_name"] for e in events] == \
        ["heal.attempt", "heal.attempt", "heal.attempt", "heal.exhausted"]
    # attempts chain: each triggered_by the previous event
    assert events[1]["triggered_by"] == events[0]["seq"]
    assert events[3]["triggered_by"] == events[2]["seq"]   # exhausted ← last attempt


def test_heal_stops_at_first_green():
    events, record = _recorder()
    gates = iter([{"passed": False, "exit_code": 1, "output": "x"},
                  {"passed": True, "exit_code": 0, "output": ""}])
    final = auto_heal_to_green(
        {"passed": False, "exit_code": 1, "output": "x"},
        run_gate=lambda: next(gates), heal_fn=lambda o: None,
        record_event=record, max_attempts=5, triggered_by=1)
    assert final["passed"] is True
    assert [e["tool_name"] for e in events] == ["heal.attempt", "heal.attempt", "heal.resolved"]


# ── agent wiring: _finish triggers the heal loop on a red gate ──────────────

class _FakeLedger:
    def __init__(self):
        self.events, self.seq = [], 0

    def record_tool_call(self, **kw):
        self.seq += 1
        self.events.append({"seq": self.seq, **kw})
        return self.seq


def test_finish_auto_heals_red_to_green(monkeypatch, tmp_path):
    from src.agent import KorgexAgent
    import src.test_gate as TG
    a = KorgexAgent(model="gpt-4o", repo_root=str(tmp_path), interactive=False)
    a.test_gate = {"command": "pytest -q"}
    a.heal_attempts = 3
    healed = {"n": 0}
    a.heal_fn = lambda output, cwd: healed.__setitem__("n", healed["n"] + 1)
    gates = iter([{"passed": False, "exit_code": 1, "output": "FAIL"},
                  {"passed": True, "exit_code": 0, "output": ""}])
    monkeypatch.setattr(TG, "run_test_gate", lambda *a, **k: next(gates))

    led = _FakeLedger()
    out = a._finish({"success": True, "result": "done"}, led, prompt_seq=1, mutated=True)

    assert out["success"] is True               # healed → edit accepted
    assert out["test_gate"]["passed"] is True
    assert healed["n"] == 1
    assert [e["tool_name"] for e in led.events] == ["test_gate", "heal.attempt", "heal.resolved"]
    # the repair chains causally off the red gate event
    assert led.events[1]["triggered_by"] == led.events[0]["seq"]


def test_finish_no_heal_when_disabled(monkeypatch, tmp_path):
    from src.agent import KorgexAgent
    import src.test_gate as TG
    a = KorgexAgent(model="gpt-4o", repo_root=str(tmp_path), interactive=False)
    a.test_gate = {"command": "pytest -q"}  # heal_attempts stays 0 (default, opt-in)
    monkeypatch.setattr(TG, "run_test_gate",
                        lambda *a, **k: {"passed": False, "exit_code": 1, "output": "FAIL"})
    led = _FakeLedger()
    out = a._finish({"success": True, "result": "done"}, led, prompt_seq=1, mutated=True)
    assert out["success"] is False              # red, healing off → rejected
    assert [e["tool_name"] for e in led.events] == ["test_gate"]
