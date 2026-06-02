"""Live task ledger — the agent's self-updating checklist (anti-drift steering).

The agent writes a task list, marks items in_progress/completed as it works, and
the list is rendered for the user + fed back into the agent each turn so it can't
drift or claim done while items remain. Pure + fully tested.
"""
from src.task_ledger import TaskLedger


def test_set_tasks_creates_numbered_pending_items():
    led = TaskLedger()
    led.set_tasks(["read code", "write test", "implement"])
    ts = led.tasks()
    assert [t.id for t in ts] == [1, 2, 3]
    assert all(t.status == "pending" for t in ts)
    assert ts[1].text == "write test"


def test_update_by_id_and_by_text():
    led = TaskLedger()
    led.set_tasks(["a", "b"])
    assert led.update(1, "in_progress").status == "in_progress"
    assert led.update("b", "completed").status == "completed"
    assert led.update(99, "completed") is None       # unknown ref → no-op
    assert led.update("1", "completed").status == "completed"  # numeric string ref


def test_open_tasks_excludes_completed():
    led = TaskLedger()
    led.set_tasks(["a", "b", "c"])
    led.update(1, "completed")
    assert [t.text for t in led.open_tasks()] == ["b", "c"]


def test_render_shows_status_symbols():
    led = TaskLedger()
    led.set_tasks(["a", "b"])
    led.update(1, "completed")
    led.update(2, "in_progress")
    r = led.render()
    assert "[x] a" in r and "[~] b" in r


def test_summary_and_all_done():
    led = TaskLedger()
    assert led.summary() == "no tasks"
    led.set_tasks(["a", "b"])
    assert led.summary() == "0/2 done" and led.all_done() is False
    led.update(1, "completed")
    led.update(2, "completed")
    assert led.summary() == "2/2 done" and led.all_done() is True


def test_set_tasks_replaces_previous_list():
    led = TaskLedger()
    led.set_tasks(["old"])
    led.set_tasks(["new1", "new2"])
    assert [t.text for t in led.tasks()] == ["new1", "new2"]
    assert [t.id for t in led.tasks()] == [1, 2]
