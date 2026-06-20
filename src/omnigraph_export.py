"""Export Korgex ledgers/receipts into Omnigraph-loadable records.

The export is intentionally privacy-preserving: it writes graph facts and hashes,
not raw prompts, tool arguments, tool results, or code. Omnigraph can then act as
the branchable context/memory layer while Korgex remains the verifiable proof
layer.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from src import ledger_spec as S
from src import receipt as RC
from src.korg_ledger import _ledger_hmac_key, load_journal_raw

SCHEMA = """node KorgexRun {
  key: String @key
  tip: String @index
  source_kind: String @index
  source_path: String?
  claim: String?
  event_count: I64
  prompts: I64
  inferences: I64
  tool_calls: I64
  cost_usd: F64
  signed_by: String?
  generated_at: String?
  summary_json: String?
}

node KorgexEvent {
  key: String @key
  run_id: String @index
  seq_id: I64
  tool_name: String @index
  success: Bool?
  duration_ms: I64?
  source_agent: String?
  triggered_by: I64?
  file_path: String?
  args_sha256: String?
  result_sha256: String?
  text: String?
}

node KorgexFile {
  path: String @key
}

edge RunHasEvent: KorgexRun -> KorgexEvent
edge EventTouchedFile: KorgexEvent -> KorgexFile
edge EventTriggered: KorgexEvent -> KorgexEvent
"""


def _sha256_json(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _verify_events(events: list[dict[str, Any]], *, key: bytes | None = None) -> None:
    errors = S.verify_dag(events) + S.verify_chain(events, key=key)
    if errors:
        raise ValueError("ledger does not verify: " + "; ".join(errors[:6]))


def _tip(events: list[dict[str, Any]]) -> str:
    return events[-1].get("entry_hash") if events else S.GENESIS_HASH


def load_source(
    path: str | os.PathLike[str], *, key: bytes | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    """Load a Korgex receipt JSON or raw journal and verify it before export."""
    p = Path(path)
    raw = p.read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict) and data.get("schema") == RC.SCHEMA:
        verdict = RC.verify_receipt(data, key=key)
        if not verdict["ok"]:
            raise ValueError("receipt does not verify: " + "; ".join(verdict["errors"][:6]))
        return list(data.get("events") or []), data, "receipt"

    events = load_journal_raw(str(p))
    _verify_events(events, key=key)
    return events, None, "journal"


def _event_id(run_id: str, seq_id: Any) -> str:
    return f"{run_id}:event:{seq_id}"


def _args_file_path(event: dict[str, Any]) -> str | None:
    args = event.get("args") or {}
    if not isinstance(args, dict):
        return None
    value = args.get("file_path") or args.get("path") or args.get("notebook_path")
    return value if isinstance(value, str) and value else None


def _short_event_text(event: dict[str, Any]) -> str:
    tool = event.get("tool_name") or event.get("event_type") or "event"
    seq = event.get("seq_id")
    path = _args_file_path(event)
    if path:
        return f"#{seq} {tool} {path}"
    return f"#{seq} {tool}"


def records_from_events(
    events: list[dict[str, Any]],
    *,
    receipt: dict[str, Any] | None = None,
    source_path: str | None = None,
    source_kind: str = "journal",
    key: bytes | None = None,
) -> list[dict[str, Any]]:
    """Convert verified events into Omnigraph JSONL records."""
    events = list(events or [])
    _verify_events(events, key=key)

    run_id = _tip(events)
    summary = RC.summarize(events)
    sig = (receipt or {}).get("signature") or {}
    run_data = {
        "key": run_id,
        "tip": run_id,
        "source_kind": source_kind,
        "source_path": source_path,
        "claim": (receipt or {}).get("claim"),
        "event_count": int(len(events)),
        "prompts": int(summary.get("prompts", 0)),
        "inferences": int(summary.get("inferences", 0)),
        "tool_calls": int(summary.get("tool_calls", 0)),
        "cost_usd": float(summary.get("cost_usd", 0.0)),
        "signed_by": sig.get("pubkey"),
        "generated_at": None if receipt is None or receipt.get("generated_at") is None else str(receipt.get("generated_at")),
        "summary_json": json.dumps(summary, sort_keys=True, separators=(",", ":")),
    }
    records: list[dict[str, Any]] = [{"type": "KorgexRun", "data": run_data}]

    seen_files: set[str] = set()
    seq_to_node: dict[Any, str] = {}
    for event in events:
        seq = event.get("seq_id")
        node_id = _event_id(run_id, seq)
        seq_to_node[seq] = node_id
        args = event.get("args")
        result = event.get("result")
        file_path = _args_file_path(event)
        event_data = {
            "key": node_id,
            "run_id": run_id,
            "seq_id": int(seq or 0),
            "tool_name": event.get("tool_name") or event.get("event_type") or "event",
            "success": event.get("success"),
            "duration_ms": event.get("duration_ms"),
            "source_agent": event.get("source_agent"),
            "triggered_by": event.get("triggered_by"),
            "file_path": file_path,
            "args_sha256": _sha256_json(args) if args is not None else None,
            "result_sha256": _sha256_json(result) if result is not None else None,
            "text": _short_event_text(event),
        }
        records.append({"type": "KorgexEvent", "data": event_data})
        records.append({"edge": "RunHasEvent", "from": run_id, "to": node_id})
        if file_path:
            if file_path not in seen_files:
                seen_files.add(file_path)
                records.append({"type": "KorgexFile", "data": {"path": file_path}})
            records.append({"edge": "EventTouchedFile", "from": node_id, "to": file_path})

    for event in events:
        seq = event.get("seq_id")
        parent = event.get("triggered_by")
        if parent is not None and parent in seq_to_node and seq in seq_to_node:
            records.append({"edge": "EventTriggered", "from": seq_to_node[parent], "to": seq_to_node[seq]})

    return records


def export_records(
    source_path: str | os.PathLike[str],
    *,
    out_path: str | os.PathLike[str],
    schema_out: str | os.PathLike[str] | None = None,
    key: bytes | None = None,
) -> dict[str, Any]:
    """Write Omnigraph JSONL and optional .pg schema for a journal/receipt."""
    key = _ledger_hmac_key() if key is None else key
    events, receipt, source_kind = load_source(source_path, key=key)
    records = records_from_events(
        events,
        receipt=receipt,
        source_path=str(source_path),
        source_kind=source_kind,
        key=key,
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    if schema_out:
        schema_path = Path(schema_out)
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(SCHEMA, encoding="utf-8")

    return {
        "out_path": str(out),
        "schema_out": str(schema_out) if schema_out else None,
        "records": len(records),
        "events": len(events),
        "files": len({r["data"]["path"] for r in records if r.get("type") == "KorgexFile"}),
        "run_id": _tip(events),
        "source_kind": source_kind,
    }


def write_to_omnigraph(
    source_path: str | os.PathLike[str],
    *,
    store: str,
    branch: str = "main",
    base: str | None = None,
    mode: str = "append",
    tmp_out: str | os.PathLike[str] | None = None,
    schema_out: str | os.PathLike[str] | None = None,
    omnigraph_bin: str = "omnigraph",
) -> dict[str, Any]:
    """Export then invoke `omnigraph load` against a graph store/server scope."""
    if mode not in {"append", "merge", "overwrite"}:
        raise ValueError("mode must be append, merge, or overwrite")
    out = Path(tmp_out or Path(".korg") / "omnigraph" / "korgex-export.jsonl")
    summary = export_records(source_path, out_path=out, schema_out=schema_out)
    cmd = [omnigraph_bin, "load", "--data", str(out), "--mode", mode, "--branch", branch]
    if base:
        cmd.extend(["--from", base])
    cmd.append(store)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    summary.update({
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    })
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "omnigraph load failed")
    return summary
