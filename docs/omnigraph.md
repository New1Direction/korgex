# Korgex × Omnigraph

Omnigraph is a branchable graph database for agent memory and multi-agent coordination. Korgex can publish a verified coding run into Omnigraph as graph facts while keeping the private transcript local.

> Korgex proves what the coding agent did. Omnigraph makes those proofs queryable as a living dev graph. NEAR can anchor the receipt root publicly.

## What Korgex exports

`korgex omnigraph export` verifies a Korgex journal or receipt, then writes Omnigraph JSONL records for:

- `KorgexRun` — the whole verified run, keyed by the ledger tip/root.
- `KorgexEvent` — each ledger event with hashes of args/results, not raw content.
- `KorgexFile` — files mentioned by read/edit/tool events.
- `RunHasEvent`, `EventTouchedFile`, `EventTriggered` — run/event/file/causal edges.

The export intentionally excludes raw prompts, tool arguments, tool results, code, and secrets.

## Export a run

```bash
korgex receipt .korg/journal.jsonl --sign \
  --claim "Korgex fixed issue #123" \
  --out .korg/receipts/issue-123.korgreceipt.json

korgex omnigraph export .korg/receipts/issue-123.korgreceipt.json \
  --out .korg/omnigraph/issue-123.jsonl \
  --schema-out .korg/omnigraph/korgex-dev.pg
```

Initialize an Omnigraph graph with the generated schema:

```bash
omnigraph init --schema .korg/omnigraph/korgex-dev.pg devgraph.omni
omnigraph load --data .korg/omnigraph/issue-123.jsonl \
  --mode append \
  --branch agent/issue-123 \
  --from main \
  devgraph.omni
```

Or let Korgex run the load step:

```bash
korgex omnigraph write .korg/receipts/issue-123.korgreceipt.json \
  --store devgraph.omni \
  --branch agent/issue-123 \
  --from main \
  --schema-out .korg/omnigraph/korgex-dev.pg
```

## Why this matters

This creates a clean three-layer architecture:

```text
Korgex     verifiable coding work and signed receipts
Omnigraph  branchable memory/context graph for agents and reviewers
NEAR       public anchoring, payment, and reputation for verified work
```

A strong demo is: GitHub issue → Korgex fix → receipt → Omnigraph branch → human/agent review → merge → NEAR anchor.
