# Korgex NEAR Anchor Contract

Minimal NEAR contract for anchoring Korgex receipt roots.

It stores only hashes and metadata:

- `ledger_root`
- `event_count`
- `journal_sha256`
- optional `receipt_sha256`
- optional `artifact_uri`
- optional `memo`
- optional `korgex_version`
- caller account and block timestamp

Do **not** send raw prompts, code, tool arguments/results, API keys, or secrets.

## Build

```bash
cargo near build non-reproducible-wasm
# or with cargo-near installed through your preferred NEAR toolchain
```

## Deploy to testnet

```bash
near create-account korgex-anchor.YOU.testnet --masterAccount YOU.testnet --initialBalance 2
near deploy korgex-anchor.YOU.testnet ./target/near/korgex_near_anchor.wasm
near call korgex-anchor.YOU.testnet new '{}' --accountId YOU.testnet
```

## Anchor a Korgex receipt

Generate an anchor payload from the Korgex repo:

```bash
korgex near anchor .korg/receipts/demo.korgreceipt.json \
  --account YOU.testnet \
  --contract korgex-anchor.YOU.testnet \
  --out .korg/near/demo-anchor.json
```

The command prints a ready-to-run `near call ...` command. The same arguments are also in `.contract_call.args` inside the JSON file.
