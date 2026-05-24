#!/usr/bin/env python3
"""
korg_dogfood.py — Agent Event Spec v1.0 Dogfood Checklist

Runs the 6 checklist items from agent_event_spec.md §6 against a live
korg ledger at http://localhost:8080.

Usage:
    python3 korg_dogfood.py [--base-url http://localhost:8080]

Prerequisite: korg running with --web, and at least one korgex session
has been run so events exist in the ledger.

Exit codes:
    0  — all checks passed
    1  — one or more checks failed (see FAIL lines)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

import requests

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"
INFO = "\033[34m·\033[0m"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    sym = PASS if ok else FAIL
    print(f"  {sym}  {label}" + (f"\n       {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def info(msg: str) -> None:
    print(f"  {INFO}  {msg}")


def warn(msg: str) -> None:
    print(f"  {WARN}  {msg}")


# ---------------------------------------------------------------------------
# Fetch the ledger
# ---------------------------------------------------------------------------

def fetch_journal(base_url: str) -> list[dict]:
    url = f"{base_url}/api/journal"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()

    events = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        events.append(json.loads(line))

    return events


# ---------------------------------------------------------------------------
# Checklist items
# ---------------------------------------------------------------------------

def check_1_backward_causal_chain(events: list[dict]) -> None:
    """Walk the causal chain from final state back to user_prompt."""
    print("\n[1] Backward causal chain (leaf → root)")

    # Find the last AgentToolCall event
    agent_events = [
        e for e in events
        if e.get("event", {}).get("event_type") == "AgentToolCall"
    ]

    if not agent_events:
        check("AgentToolCall events present", False, "No agent events found — run a korgex session first")
        return

    check("AgentToolCall events present", True, f"{len(agent_events)} events found")

    # Build seq_id → event index
    by_seq: dict[int, dict] = {e["seq_id"]: e for e in events}

    # Walk backward from the last agent event
    leaf = agent_events[-1]
    chain: list[int] = []
    current = leaf
    max_hops = 200

    for _ in range(max_hops):
        chain.append(current["seq_id"])
        tb = current.get("metadata", {}).get("triggered_by")
        if tb is None:
            break
        parent = by_seq.get(tb)
        if parent is None:
            warn(f"Dead pointer: seq={current['seq_id']} has triggered_by={tb} but seq={tb} not in ledger")
            warn("This is expected if the parent was dropped (spec §7.5 drop-oldest policy)")
            break
        current = parent

    root_tool = current.get("event", {}).get("tool_name", "?")
    check(
        "Backward walk reaches a root event",
        root_tool == "user_prompt" or current["metadata"]["triggered_by"] is None,
        f"chain depth={len(chain)}, root tool_name={root_tool!r}"
    )
    info(f"Chain: {' → '.join(str(s) for s in reversed(chain))}")

    check(
        "Root event has triggered_by=None",
        current["metadata"].get("triggered_by") is None,
        f"root seq={current['seq_id']}"
    )


def check_2_forward_causal_chain(events: list[dict]) -> None:
    """Walk the causal chain forward from user_prompt (debugger story)."""
    print("\n[2] Forward causal chain (root → leaves)")

    # Build triggered_by index: parent_seq → [child_events]
    children: dict[int | None, list[dict]] = defaultdict(list)
    for e in events:
        tb = e.get("metadata", {}).get("triggered_by")
        children[tb].append(e)

    # Find root AgentToolCall events: triggered_by=None AND tool_name=user_prompt
    # Note: triggered_by=None is stored as JSON null; Python reads as None.
    agent_roots = [
        e for e in events
        if e.get("metadata", {}).get("triggered_by") is None
        and e.get("event", {}).get("event_type") == "AgentToolCall"
        and e.get("event", {}).get("tool_name") == "user_prompt"
    ]

    # Also accept any AgentToolCall with triggered_by=None as a root
    any_agent_roots = [
        e for e in events
        if e.get("metadata", {}).get("triggered_by") is None
        and e.get("event", {}).get("event_type") == "AgentToolCall"
    ]

    check(
        "user_prompt root event(s) exist",
        len(agent_roots) > 0,
        f"found {len(agent_roots)} root user_prompt event(s) "
        f"(any AgentToolCall roots: {len(any_agent_roots)})"
    )

    if not any_agent_roots:
        warn(
            "No AgentToolCall events with triggered_by=None found. "
            "If korg was running before the session was injected, root events "
            "may have been chained to prior internal events — run with a fresh korg."
        )
        return

    root = any_agent_roots[-1]
    # BFS forward
    visited: list[int] = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        visited.append(node["seq_id"])
        for child in children.get(node["seq_id"], []):
            queue.append(child)

    check(
        "Forward walk finds at least 2 descendant events",
        len(visited) >= 2,
        f"reachable from root seq={root['seq_id']}: {visited[:10]}{'...' if len(visited) > 10 else ''}"
    )

    info(f"Forward walk visited {len(visited)} events from root seq={root['seq_id']}")
    if len(events) > 50 and len(visited) < len(events):
        warn(
            "Forward walk requires O(n) scan over full ledger. "
            "Consider adding a triggered_by index to korg's journal handler "
            "before the demo (spec §6 forward-walk note)."
        )


def check_3_file_query(events: list[dict]) -> None:
    """Query 'every event that touched a file' using args.file_path."""
    print("\n[3] File-touch query (args.file_path)")

    agent_events = [
        e for e in events
        if e.get("event", {}).get("event_type") == "AgentToolCall"
    ]

    # Collect all file paths mentioned in args
    file_touches: dict[str, list[int]] = defaultdict(list)
    for e in agent_events:
        args = e.get("event", {}).get("args", {})
        if isinstance(args, dict):
            fp = args.get("file_path") or args.get("path") or args.get("filepath")
            if fp and isinstance(fp, str) and not fp.startswith("sha256:"):
                file_touches[fp].append(e["seq_id"])

    check(
        "File-path args present in at least one event",
        len(file_touches) > 0,
        f"{len(file_touches)} unique file(s) touched: {list(file_touches.keys())[:5]}"
    )

    if file_touches:
        sample_file = next(iter(file_touches))
        seqs = file_touches[sample_file]
        info(f"Events touching {sample_file!r}: seq={seqs}")

        # Verify content-ref discipline: no file > 1KB inlined
        for e in agent_events:
            args = e.get("event", {}).get("args", {})
            result = e.get("event", {}).get("result", {})
            for field_name, field in [("args", args), ("result", result)]:
                if isinstance(field, dict):
                    for k, v in field.items():
                        if isinstance(v, str) and len(v.encode()) > 1024:
                            check(
                                f"No inline payload >1KB in seq={e['seq_id']}.{field_name}.{k}",
                                False,
                                f"Value is {len(v.encode())} bytes — should be content-referenced"
                            )


def check_4_blob_atomicity(events: list[dict], base_url: str) -> None:
    """Confirm content refs point to existing blobs (blob-first atomicity)."""
    print("\n[4] Blob atomicity (content refs → blobs exist)")

    import os
    from pathlib import Path

    agent_events = [
        e for e in events
        if e.get("event", {}).get("event_type") == "AgentToolCall"
    ]

    refs_found = 0
    missing = []

    blob_root = Path(os.environ.get("KORG_BLOB_DIR", ".korg/blobs"))

    for e in agent_events:
        for ref in e.get("event", {}).get("payload_refs", []):
            sha256 = ref.get("sha256", "")
            refs_found += 1
            blob_path = blob_root / sha256[:2] / sha256
            if not blob_path.exists():
                missing.append((e["seq_id"], sha256[:16] + "..."))

    if refs_found == 0:
        info("No payload_refs in ledger yet — no blob atomicity to verify (need larger payloads)")
    else:
        check(
            f"All {refs_found} content refs have corresponding blobs",
            len(missing) == 0,
            f"Missing: {missing}" if missing else ""
        )


def check_5_actor_convention(events: list[dict]) -> None:
    """Confirm actor_id format is consistent across a full session."""
    print("\n[5] Actor identity convention")

    agent_events = [
        e for e in events
        if e.get("event", {}).get("event_type") == "AgentToolCall"
    ]

    if not agent_events:
        check("AgentToolCall events present for actor check", False)
        return

    source_agents = {e["event"]["source_agent"] for e in agent_events}
    info(f"source_agent values seen: {source_agents}")

    VALID_PREFIXES = ("agent:", "human:", "korg:", "mcp:")
    bad = [s for s in source_agents if not any(s.startswith(p) for p in VALID_PREFIXES)]
    check(
        "All source_agent values follow convention (agent:/human:/korg:/mcp:)",
        len(bad) == 0,
        f"Non-conforming: {bad}" if bad else ""
    )

    # Check schema_version presence
    schema_versions = {e.get("schema_version", "MISSING") for e in events}
    check(
        "schema_version present on all events",
        "MISSING" not in schema_versions,
        f"versions seen: {schema_versions}"
    )


def check_6_forward_index_performance(events: list[dict]) -> None:
    """Check if forward walk by triggered_by would require O(n) scan."""
    print("\n[6] Forward-walk index readiness")

    agent_events = [
        e for e in events
        if e.get("event", {}).get("event_type") == "AgentToolCall"
    ]

    if len(events) < 10:
        info(f"Only {len(events)} events — forward index won't matter until scale")
        return

    # Check if /api/journal supports triggered_by filtering (future enhancement)
    total = len(events)
    agent_count = len(agent_events)
    info(f"Total events: {total}, AgentToolCall events: {agent_count}")

    if total > 100:
        warn(
            f"Ledger has {total} events. Forward walk (find all children of seq=N) "
            "requires O(n) scan of the full journal. "
            "Add a GET /api/journal?triggered_by=N endpoint before the demo."
        )
    else:
        check(
            "Ledger small enough that O(n) forward scan is acceptable",
            True,
            f"{total} events — add index before scaling past ~500"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="korg dogfood checklist (spec §6)")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--task", help="Optional: task used to generate the session")
    args = parser.parse_args()

    print(f"\n{'━'*60}")
    print(f"  korg dogfood checklist — agent_event_spec.md §6")
    print(f"  ledger: {args.base_url}")
    print(f"{'━'*60}")

    try:
        events = fetch_journal(args.base_url)
    except Exception as e:
        print(f"\n  {FAIL}  Cannot reach korg at {args.base_url}: {e}")
        print("       Start korg with: cargo run -- --web")
        sys.exit(1)

    print(f"\n  Fetched {len(events)} events from ledger")

    check_1_backward_causal_chain(events)
    check_2_forward_causal_chain(events)
    check_3_file_query(events)
    check_4_blob_atomicity(events, args.base_url)
    check_5_actor_convention(events)
    check_6_forward_index_performance(events)

    print(f"\n{'━'*60}")
    if failures:
        print(f"  {FAIL}  {len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"       • {f}")
        sys.exit(1)
    else:
        print(f"  {PASS}  All checks passed")
    print(f"{'━'*60}\n")


if __name__ == "__main__":
    main()
