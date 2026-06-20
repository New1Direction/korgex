"""Runnable demo script generators for Korgex product workflows."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NearOmnigraphDemoOptions:
    journal: str = ".korg/journal.jsonl"
    receipt: str = ".korg/receipts/near-omnigraph-demo.korgreceipt.json"
    receipt_html: bool = True
    omnigraph_jsonl: str = ".korg/omnigraph/near-omnigraph-demo.jsonl"
    omnigraph_schema: str = ".korg/omnigraph/korgex-dev.pg"
    omnigraph_store: str = "devgraph.omni"
    omnigraph_branch: str = "agent/near-omnigraph-demo"
    near_anchor: str = ".korg/near/near-omnigraph-demo-anchor.json"
    near_account: str = "YOU.testnet"
    near_contract: str = "korgex-anchor.YOU.testnet"
    claim: str = "Korgex fixed code, exported agent memory, and anchored the receipt root"


def near_omnigraph_script(opts: NearOmnigraphDemoOptions | None = None) -> str:
    opts = opts or NearOmnigraphDemoOptions()
    html_flag = " \\\n  --html" if opts.receipt_html else ""
    return f"""#!/usr/bin/env bash
set -euo pipefail

# Korgex × Omnigraph × NEAR demo
# 1) prove a coding-agent run, 2) write graph facts to Omnigraph, 3) anchor hashes on NEAR.

# If you do not have a run yet, create one first, for example:
#   korgex \"make one tiny safe code improvement and run its focused tests\"

# Verify the local Korgex journal before exporting anything.
korgex verify {opts.journal}

# Mint a signed portable receipt. The receipt embeds the verifiable event chain.
korgex receipt {opts.journal} \\
  --sign \\
  --claim {opts.claim!r} \\
  --out {opts.receipt}{html_flag}

# Export privacy-preserving records for Omnigraph: metadata + hashes, not raw prompts/code/tool output.
korgex omnigraph export {opts.receipt} \\
  --out {opts.omnigraph_jsonl} \\
  --schema-out {opts.omnigraph_schema}

# Initialize the Omnigraph dev graph once. If it already exists, this may fail safely; keep going.
omnigraph init --schema {opts.omnigraph_schema} {opts.omnigraph_store} || true

# Load the run into an isolated agent branch for review/merge.
omnigraph load --data {opts.omnigraph_jsonl} \\
  --mode append \\
  --branch {opts.omnigraph_branch} \\
  --from main \\
  {opts.omnigraph_store}

# Create a NEAR anchor payload. It contains hashes only.
korgex near anchor {opts.receipt} \\
  --account {opts.near_account} \\
  --contract {opts.near_contract} \\
  --memo 'Korgex verified coding receipt' \\
  --out {opts.near_anchor}

# The previous command prints the final near-cli command.
# Deploy the example contract first if needed:
#   cd examples/near-anchor-contract
#   cargo near build non-reproducible-wasm
#   near deploy {opts.near_contract} ./target/near/korgex_near_anchor.wasm
#   near call {opts.near_contract} new '{{}}' --accountId {opts.near_account}
"""
