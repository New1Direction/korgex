# Demo: Korgex → Omnigraph → NEAR

This is the shortest pitchable workflow for Korgex as verifiable agent work:

```text
Korgex fixes code → Korgex signs a receipt → Omnigraph stores graph facts on an agent branch → NEAR anchors the receipt root
```

## 0. Generate the script

```bash
korgex demo near-omnigraph \
  --account YOU.testnet \
  --contract korgex-anchor.YOU.testnet \
  --store devgraph.omni \
  --branch agent/demo \
  --write .korg/demos/near-omnigraph.sh
```

Review it, then run:

```bash
.korg/demos/near-omnigraph.sh
```

## 1. Run Korgex on a real task

```bash
korgex "make one tiny safe code improvement and run its focused tests"
```

This creates or appends to `.korg/journal.jsonl`.

## 2. Mint a signed receipt

```bash
korgex verify .korg/journal.jsonl
korgex receipt .korg/journal.jsonl \
  --sign \
  --claim "Korgex fixed code, exported agent memory, and anchored the receipt root" \
  --out .korg/receipts/near-omnigraph-demo.korgreceipt.json \
  --html
```

## 3. Export to Omnigraph

```bash
korgex omnigraph export .korg/receipts/near-omnigraph-demo.korgreceipt.json \
  --out .korg/omnigraph/near-omnigraph-demo.jsonl \
  --schema-out .korg/omnigraph/korgex-dev.pg

omnigraph init --schema .korg/omnigraph/korgex-dev.pg devgraph.omni || true
omnigraph load --data .korg/omnigraph/near-omnigraph-demo.jsonl \
  --mode append \
  --branch agent/near-omnigraph-demo \
  --from main \
  devgraph.omni
```

Omnigraph now has queryable graph facts for the run, event chain, files touched, and causal edges — without raw prompts, code, tool args/results, or secrets.

## 4. Deploy the NEAR anchor contract

The example contract lives at [`examples/near-anchor-contract`](../examples/near-anchor-contract/README.md).

```bash
cd examples/near-anchor-contract
cargo near build non-reproducible-wasm
near create-account korgex-anchor.YOU.testnet --masterAccount YOU.testnet --initialBalance 2
near deploy korgex-anchor.YOU.testnet ./target/near/korgex_near_anchor.wasm
near call korgex-anchor.YOU.testnet new '{}' --accountId YOU.testnet
cd ../..
```

## 5. Anchor the receipt root on NEAR

```bash
korgex near anchor .korg/receipts/near-omnigraph-demo.korgreceipt.json \
  --account YOU.testnet \
  --contract korgex-anchor.YOU.testnet \
  --memo "Korgex verified coding receipt" \
  --out .korg/near/near-omnigraph-demo-anchor.json
```

The command prints a ready-to-run `near call ...` command. Run it to publish the anchor.

## 6. The pitch

> Korgex is a coding agent that keeps cryptographic receipts. Omnigraph turns those verified runs into a branchable agent memory graph. NEAR anchors the proof publicly so agent work can become payable and reputation-bearing.
