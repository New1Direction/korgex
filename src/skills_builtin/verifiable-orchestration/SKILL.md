---
name: verifiable-orchestration
description: Run multi-agent work as ONE provable causal DAG — parallel subagents, an Orchestrate graph, an immutable spec-seed
version: 1.0
trust: built-in
---

korgex can split work across subagents and record the whole run as one connected,
tamper-evident causal DAG — replayable and auditable (`korgex trace`/`verify`).
Pick the right shape:

- **Independent, flat sub-tasks → the `Agent` tool.** Emit several `Agent` calls in
  ONE turn and they fan out concurrently (results return in order; a crashed
  sibling is isolated). Use for "explore these 3 areas", "review these 4 files".
- **Sub-tasks with DEPENDENCIES → the `Orchestrate` tool.** Give it `nodes` with
  `deps` (e.g. `explore → plan → implement → review`): independent nodes run in
  parallel, a node waits for its deps, and if a node fails its dependents are
  **skipped** rather than run against a broken precondition. The whole run — including
  the failure topology — is one verifiable DAG.

Lock intent first, for non-trivial runs:
- Pass **`seed`** to `Orchestrate` — the agreed `{goal, constraints, acceptance_criteria}`.
  It's recorded as an immutable, hash-chained `spec.seed` the whole run anchors
  under, so `korgex why`/`trace` walk any result back to the spec it was meant to
  satisfy and `korgex verify` proves the spec wasn't altered after the fact. Use it
  whenever the "what we agreed to build" should be pinned before work starts.

Rules:
- **One level deep.** A subagent cannot itself spawn agents or orchestrate
  (hard-enforced) — keep the graph at the top level.
- Each node gets a tool surface scoped to its type (explore/plan/review/research are
  read-only; code gets the full set).
- Prefer a few well-scoped nodes over many tiny ones; name dependencies explicitly.
- After the run, the result carries `root_seq` (and `seed_seq`) — trace from there to
  audit exactly what each subagent did and why.
