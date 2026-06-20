# Korgex × NEAR

Korgex is a terminal coding agent that keeps cryptographic receipts for what it did. NEAR can make those receipts public, timestamped, payable, and reputation-bearing without exposing the private session log.

> IronClaw protects the agent while it acts. Korgex proves what the agent did. NEAR anchors that proof and can pay for verified work.

## Why this fits NEAR AI

Illia Polosukhin's NEAR AI thesis is that AI becomes the interface and blockchain becomes the backend/root of trust for agents. Korgex contributes the audit layer for software work:

- **Verifiable agent work** — every read/edit/test/tool action is hash-chained in `korg-ledger@v1`.
- **User-owned by default** — logs stay local; only hashes are anchored.
- **Agent marketplace primitive** — GitHub issue → Korgex fix → tests → receipt → NEAR payment/reputation.
- **IronClaw-compatible shape** — Korgex can be wrapped as a coding tool/worker whose outputs are independently checkable.

## What goes on-chain

The NEAR anchor payload intentionally contains only hashes and metadata:

```json
{
  "ledger_root": "<hash-chain tip>",
  "event_count": 42,
  "journal_sha256": "<hash of local journal events>",
  "receipt_sha256": "<hash of signed receipt, optional>",
  "artifact_uri": "<optional encrypted receipt/proof URL>",
  "memo": "fixed issue #123",
  "korgex_version": "0.35.0"
}
```

It excludes prompts, code, tool arguments, tool results, private keys, API tokens, and secrets.

## Demo workflow

```bash
# 1. Run Korgex on a real coding task.
korgex "fix the failing NEAR contract test and run the suite"

# 2. Prove the local journal is intact.
korgex verify .korg/journal.jsonl

# 3. Mint a signed portable receipt.
korgex receipt .korg/journal.jsonl --sign \
  --claim "Korgex fixed the NEAR contract test" \
  --out .korg/receipts/near-demo.korgreceipt.json \
  --html

# 4. Create the NEAR anchor payload.
korgex near anchor .korg/receipts/near-demo.korgreceipt.json \
  --account you.testnet \
  --contract korgex-anchor.testnet \
  --memo "Korgex verified coding receipt" \
  --out .korg/near/near-demo-anchor.json
```

The command prints a `near call ...` example. You can use that directly with near-cli-js, adapt it for near-cli-rs, or send the `contract_call.args` through a wallet/intent flow.

## Minimal contract interface

Any NEAR contract can store the anchor if it exposes a method like:

```rust
pub fn anchor(
    &mut self,
    ledger_root: String,
    event_count: u64,
    journal_sha256: String,
    receipt_sha256: Option<String>,
    artifact_uri: Option<String>,
    memo: Option<String>,
    korgex_version: Option<String>,
) {
    // validate 64-char hex fields, attach predecessor_account_id + block timestamp,
    // then persist under ledger_root or emit an event log.
}
```

For a first NEAR demo, the contract only needs to persist these fields plus `env::predecessor_account_id()` and `env::block_timestamp_ms()`.

## Pitch to NEAR / Illia

Short version:

> Korgex is an open-source coding agent with a tamper-evident causal ledger. It records every action, test, and edit as verifiable agent work. I’m adding NEAR anchoring so coding-agent receipts can become public proof-of-work for the NEAR AI economy: agents do tasks, Korgex proves the work, NEAR anchors the proof and coordinates payment/reputation.

A good 2-minute demo is: GitHub issue → Korgex fix → tests pass → signed receipt → NEAR testnet anchor.
