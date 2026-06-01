"""Background shell tasks — run a long command without blocking the agent.

A launched command runs in a daemon thread; output streams into the task as it
runs (so a poll shows partial output), and status flips to done/failed on exit.
Tests synchronize on a completion Event (never sleep-guess).
"""
from src.background_tasks import BackgroundRunner


def test_launch_returns_id_and_completes(tmp_path):
    r = BackgroundRunner()
    tid = r.launch("echo hello", cwd=str(tmp_path))
    assert tid.startswith("bg_")
    assert r.wait(tid, timeout=10)               # completes within timeout
    t = r.get(tid)
    assert t.status == "done" and t.exit_code == 0 and "hello" in t.output


def test_failed_command_reports_failure(tmp_path):
    r = BackgroundRunner()
    tid = r.launch("echo oops; exit 3", cwd=str(tmp_path))
    r.wait(tid, timeout=10)
    t = r.get(tid)
    assert t.status == "failed" and t.exit_code == 3 and "oops" in t.output


def test_poll_returns_status_snapshot(tmp_path):
    r = BackgroundRunner()
    tid = r.launch("echo a; echo b", cwd=str(tmp_path))
    r.wait(tid, timeout=10)
    snap = r.poll(tid)
    assert snap["status"] == "done" and "a" in snap["output"] and "b" in snap["output"]


def test_get_unknown_is_none():
    assert BackgroundRunner().get("bg_nope") is None
    assert BackgroundRunner().poll("bg_nope") is None


def test_all_lists_launched_tasks(tmp_path):
    r = BackgroundRunner()
    a = r.launch("echo 1", cwd=str(tmp_path))
    b = r.launch("echo 2", cwd=str(tmp_path))
    r.wait(a, timeout=10)
    r.wait(b, timeout=10)
    ids = {t.id for t in r.all()}
    assert {a, b} <= ids


def test_running_task_is_running_then_done(tmp_path):
    r = BackgroundRunner()
    tid = r.launch("sleep 0.3; echo ok", cwd=str(tmp_path))
    assert r.get(tid).status == "running"        # not blocking — still running right after launch
    assert r.wait(tid, timeout=10)
    assert r.get(tid).status == "done" and "ok" in r.get(tid).output
