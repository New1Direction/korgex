"""Background shell tasks — run long commands (builds, test suites, dev servers,
watchers) without blocking the agent's turn.

`launch()` spawns the command in a daemon thread and returns immediately with a
task id. Output streams into the task as it runs (a `poll()` shows partial
output), and status flips to done/failed when it exits. The agent checks back with
the BashOutput tool; the REPL lists jobs with `/jobs`.
"""
from __future__ import annotations

import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field


@dataclass
class BgTask:
    id: str
    command: str
    status: str = "running"          # running | done | failed
    output: str = ""
    exit_code: int | None = None
    _done: threading.Event = field(default_factory=threading.Event, repr=False)


class BackgroundRunner:
    """Process-wide registry of background shell tasks."""

    def __init__(self):
        self._tasks: dict = {}
        self._lock = threading.Lock()

    def launch(self, command: str, cwd: str = None) -> str:
        tid = "bg_" + uuid.uuid4().hex[:8]
        task = BgTask(id=tid, command=command)
        with self._lock:
            self._tasks[tid] = task
        threading.Thread(target=self._run, args=(task, command, cwd or os.getcwd()),
                         daemon=True).start()
        return tid

    def _run(self, task: BgTask, command: str, cwd: str) -> None:
        try:
            proc = subprocess.Popen(["bash", "-c", command], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, cwd=cwd, bufsize=1)
            for line in proc.stdout:           # stream output as it arrives
                with self._lock:
                    task.output += line
            proc.wait()
            task.exit_code = proc.returncode
            task.status = "done" if proc.returncode == 0 else "failed"
        except Exception as e:
            task.output += f"\n[background task error: {type(e).__name__}: {e}]"
            task.exit_code = -1
            task.status = "failed"
        finally:
            task._done.set()

    def get(self, tid: str):
        return self._tasks.get(tid)

    def all(self) -> list:
        return list(self._tasks.values())

    def poll(self, tid: str):
        t = self._tasks.get(tid)
        if t is None:
            return None
        with self._lock:
            return {"id": t.id, "command": t.command, "status": t.status,
                    "exit_code": t.exit_code, "output": t.output}

    def wait(self, tid: str, timeout: float = 30) -> bool:
        """Block until the task finishes (or timeout). Returns True if it finished."""
        t = self._tasks.get(tid)
        return bool(t and t._done.wait(timeout))


_runner = None


def get_runner() -> BackgroundRunner:
    global _runner
    if _runner is None:
        _runner = BackgroundRunner()
    return _runner
