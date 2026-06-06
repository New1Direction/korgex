# Verify korg ledger — GitHub Action

A CI gate that **verifies what your AI agent actually did**. Point it at a korgex
receipt or ledger journal; it recomputes the hash-chain (+ causal DAG, + Ed25519
signature if present) and **fails the build if anything was tampered** — with zero
trust in the tool that produced the ledger.

It runs one of the three independent `korg-ledger@v1` implementations (Rust
`korg-verify` from crates.io by default, or the `@korgg/ledger-verify` JS impl), all
of which reproduce the same frozen conformance vectors.

## Usage

```yaml
# .github/workflows/verify.yml
name: verify-ai-work
on: [push, pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: New1Direction/korgex/.github/actions/verify-ledger@main
        with:
          path: ".korg/journal.json"        # or "**/*.korgreceipt.json"
```

Pin the signer (recommended for signed receipts), so a green check proves authorship
against a key you trust:

```yaml
      - uses: New1Direction/korgex/.github/actions/verify-ledger@main
        with:
          path: "deliverable.korgreceipt.json"
          pubkey: ${{ vars.KORG_SIGNER_PUBKEY }}
```

## Inputs

| Input | Default | Description |
|---|---|---|
| `path` | `.korg/journal.json` | File or glob to verify (receipt or journal; `**` supported). |
| `pubkey` | — | Hex pubkey to **pin** the expected signer; any other key is rejected. |
| `verifier` | `cargo` | `cargo` (korg-verify from crates.io) or `npx` (`@korgg/ledger-verify`). |
| `fail-on-invalid` | `true` | Fail the job on a bad verdict. Set `false` to report-only. |
| `summary` | `true` | Write a verdict table to the GitHub step summary. |

## Exit codes

`0` every file valid · `1` at least one invalid (the gate) · `2` setup error (no file
matched, or the verifier couldn't be installed).

## What a green check proves

The recorded events hash-chain intact and form a well-formed causal DAG
(tamper-evident); a receipt's recorded tip matches the chain head; and — if signed —
the named key attests to that exact tip. It does **not** prove *when* it happened
(needs an external time anchor) or that the key maps to a real-world identity (pin it
with `pubkey`).

> Tip: the same logic runs locally — `KORG_VERIFY_BIN=/path/to/korg-verify python3
> verify_ledger.py` (with `INPUT_PATH=…`) — so you can reproduce a CI verdict by hand.
