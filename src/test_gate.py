"""
test_gate.py — execution-grounded verification gate (roadmap Gate B).

The single highest-leverage reliability mechanism: after a run that edited
files, run the project's test/lint command and let the EXIT CODE — not the
model's self-assessment — decide whether the edit is accepted. A red gate
forces the run's success to False so a broken self-edit is never offered for
merge. Config lives in <repo_root>/.korgex/settings.json:

    { "testGate": { "command": "pytest -q && ruff check .", "timeout": 600 } }
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECS = 600


def load_test_gate(repo_root: str):
    """Load the testGate config from <repo_root>/.korgex/settings.json, or None.

    Missing file / malformed JSON / no testGate key → None (the gate is opt-in
    and must never crash a run).
    """
    path = Path(repo_root) / ".korgex" / "settings.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("[test-gate] ignoring malformed %s: %s", path, exc)
        return None
    gate = data.get("testGate")
    if isinstance(gate, dict) and gate.get("command"):
        return gate
    return None


def run_test_gate(command: str, cwd: str = None,
                  timeout: float = DEFAULT_TIMEOUT_SECS) -> dict:
    """Run the gate command in `cwd`. Returns {passed, exit_code, output}.

    passed is True only on exit code 0. Never raises — a timeout or spawn
    failure is reported as not-passed so the gate fails safe (blocks acceptance).
    """
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit_code": -1,
                "output": f"test gate timed out after {timeout}s"}
    except Exception as exc:
        return {"passed": False, "exit_code": -1,
                "output": f"test gate failed to run: {exc}"}

    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return {"passed": proc.returncode == 0, "exit_code": proc.returncode, "output": output}
