# witness — tap any tool-dispatch into a verifiable korg-ledger chain

Turn any tool-running loop — an MCP server, an agent, a CLI router — into
**verifiable evidence**: a tamper-evident, replayable, shareable record of exactly
what ran and in what causal order.

Most tools log to a plain file: any line can be edited, deleted, or reordered
undetectably. korg-ledger@v1 adds the missing property — a hash chain — so the
record can be *proven* intact and rendered into a report anyone can open and verify
in their own browser.

Two ways in. Use either or both.

## Path A — live tap (every call chained as it happens)

`witness.py` is self-contained (stdlib only, no korgex dependency). Adopt with
**two lines** at your dispatch choke point, right after `handle_tool` is defined:

```python
from witness import tap
handle_tool = tap(handle_tool)
```

Then enable it per-session:

```bash
export KORG_TAP_JOURNAL=~/runs/session-$(date +%F).korg.jsonl
export KORG_LEDGER_HMAC_KEY=…          # optional → tamper-PROOF, not just tamper-evident
```

Guarantees that matter for wrapping a production dispatcher:
- **Disabled by default** — no `$KORG_TAP_JOURNAL`, no-op, zero overhead.
- **Pass-through** — the wrapped dispatch returns the underlying result unchanged.
- **Fail-safe** — a ledger error can never break a tool call (logging is best-effort).
- **Resumes** the chain across restarts; large results stored as a content-hash ref.

## Path B — import an existing journal (no code changes)

If you already have a tool-event log (one JSON object per line: `tool`, `action`,
optional `target`/`artifact`/`artifact_hash`/`metadata`/`parent_id`/timestamps),
replay it into a hash-chained journal:

```bash
korgex import witness path/to/events.jsonl -o session.korg.jsonl
korgex verify session.korg.jsonl
korgex audit --html report.html        # self-verifying, shareable proof
```

The adapter (`korgex` `src/import_adapters.py: parse_witness`) maps
`tool → tool_name`, `{action,target,metadata} → args`,
`{artifact,artifact_hash,hostname,timestamps} → result`, and reconstructs
`parent_id → triggered_by` causal links.

## Files

| File | What |
|---|---|
| `witness.py` | the vendored korg-ledger@v1 writer + `tap()` wrapper (Path A) |
| `test_witness.py` | tests; cross-verify the writer vs korgex's `ledger_spec` |
| `demo.py` | runnable end-to-end demo on mock tools (no real data) |

Run from the korgex repo root:

```bash
python3.11 integrations/witness/demo.py
python3.11 -m pytest integrations/witness/test_witness.py tests/test_import_witness.py
```

Your data never leaves your machine; you decide what any report contains and who
you share it with.
