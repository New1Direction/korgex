#!/usr/bin/env python3
"""
inject_session.py — Inject a realistic korgex session into the korg ledger.

Simulates a full agent session without requiring an LLM API key:
  seq=1  user_prompt         (root, triggered_by=None)
  seq=2  llm_inference       (triggered_by=1)
  seq=3  Read(README.md)     (triggered_by=2, sibling of seq=4)
  seq=4  Glob(**/*.py)       (triggered_by=2, sibling of seq=3)
  seq=5  llm_inference       (triggered_by=2, next round)
  seq=6  Edit(src/routes.py) (triggered_by=5)
  seq=7  Bash(pytest)        (triggered_by=5, sibling of seq=6)
  seq=8  llm_inference       (triggered_by=5)
  seq=9  final reply         (no tools, session ends)

This exercises:
- Root event with triggered_by=None
- Parallel tool calls sharing a triggered_by (seq 3+4 both point at seq=2)
- Multi-round causality (seq=5 triggered_by=2, advancing to next prompt_seq)
- actor identity convention
- schema_version on all events
"""

from __future__ import annotations

import json
import sys
import time
import requests

BASE_URL = "http://localhost:8080"
ENDPOINT = f"{BASE_URL}/api/agent/tool-call"

# Large enough payload to trigger content-ref (>1024 bytes)
LARGE_README = "# korgex\n\n" + ("This is a long README. " * 60)


def post(payload: dict) -> int | None:
    try:
        resp = requests.post(ENDPOINT, json=payload, timeout=5)
        resp.raise_for_status()
        seq_id = resp.json()["seq_id"]
        print(f"  seq={seq_id:3d}  {payload['tool_name']:<20s}  triggered_by={payload.get('triggered_by', 'None')}")
        return seq_id
    except Exception as e:
        print(f"  ERROR posting {payload['tool_name']}: {e}", file=sys.stderr)
        return None


def main() -> None:
    print(f"\nInjecting dogfood session into {BASE_URL}\n")

    # seq=1: user_prompt (root)
    s1 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "user_prompt",
        "args": {"prompt": "Add a /healthz endpoint to src/routes.py and run tests"},
        "result": {},
        "success": True,
        "duration_ms": 0,
    })

    if s1 is None:
        print("Cannot reach korg — is it running with --web?")
        sys.exit(1)

    # seq=2: first LLM call (triggered_by=user_prompt)
    s2 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "llm_inference",
        "args": {"model": "claude-sonnet-4-6", "prompt_tokens": 412},
        "result": {"completion_tokens": 87},
        "success": True,
        "duration_ms": 1840,
        "triggered_by": s1,
    })

    # seq=3+4: parallel tool calls (both triggered_by=s2, siblings)
    s3 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "Read",
        "args": {"file_path": "README.md"},
        "result": {"content": LARGE_README},  # >1KB → should be content-ref'd in real client
        "success": True,
        "duration_ms": 12,
        "triggered_by": s2,
    })

    s4 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "Glob",
        "args": {"pattern": "**/*.py", "path": "src/"},
        "result": {"files": ["src/routes.py", "src/auth.py", "src/main.py"]},
        "success": True,
        "duration_ms": 8,
        "triggered_by": s2,  # sibling of seq=3
    })

    # seq=5: second LLM call (triggered_by advances to s2 per agent.py prompt_seq logic)
    s5 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "llm_inference",
        "args": {"model": "claude-sonnet-4-6", "prompt_tokens": 891},
        "result": {"completion_tokens": 143},
        "success": True,
        "duration_ms": 2310,
        "triggered_by": s2,
    })

    # seq=6+7: parallel — Edit + Bash both triggered_by=s5
    s6 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "Edit",
        "args": {
            "file_path": "src/routes.py",
            "old_string": "# routes",
            "new_string": "# routes\n\n@app.route('/healthz')\ndef healthz():\n    return 'ok', 200",
        },
        "result": {"success": True},
        "success": True,
        "duration_ms": 23,
        "triggered_by": s5,
    })

    s7 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "Bash",
        "args": {"command": "python -m pytest tests/ -x -q 2>&1 | tail -5"},
        "result": {"exit_code": 0, "stdout": "5 passed in 0.41s"},
        "success": True,
        "duration_ms": 1820,
        "triggered_by": s5,  # sibling of seq=6
    })

    # seq=8: third LLM call
    s8 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "llm_inference",
        "args": {"model": "claude-sonnet-4-6", "prompt_tokens": 1203},
        "result": {"completion_tokens": 56},
        "success": True,
        "duration_ms": 980,
        "triggered_by": s5,
    })

    # seq=9: final reply (no tools — just record the session end)
    s9 = post({
        "source_agent": "agent:korgex@dev",
        "tool_name": "session_complete",
        "args": {"iterations": 3},
        "result": {
            "summary": "Added /healthz endpoint to src/routes.py. Tests pass (5/5)."
        },
        "success": True,
        "duration_ms": 0,
        "triggered_by": s8,
    })

    print(f"\nSession injected. {len([x for x in [s1,s2,s3,s4,s5,s6,s7,s8,s9] if x])} events written.")
    print(f"\nExpected causal tree:")
    print(f"  seq={s1} user_prompt")
    print(f"  └─ seq={s2} llm_inference")
    print(f"     ├─ seq={s3} Read(README.md)     [sibling]")
    print(f"     ├─ seq={s4} Glob(**/*.py)       [sibling]")
    print(f"     └─ seq={s5} llm_inference")
    print(f"        ├─ seq={s6} Edit(routes.py)  [sibling]")
    print(f"        ├─ seq={s7} Bash(pytest)     [sibling]")
    print(f"        └─ seq={s8} llm_inference")
    print(f"           └─ seq={s9} session_complete")
    print()


if __name__ == "__main__":
    main()
