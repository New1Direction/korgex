# korgex — domain & architecture vocabulary

Names for the seams that matter. Architecture terms follow the `/codebase-design`
glossary (module, interface, depth, seam, adapter, leverage, locality); domain
terms below name korgex's own concepts so reviews and code use one language.

> Seeded 2026-06-22 during an architecture review (the ToolGate deepening).
> Extend lazily — add a term the first time a deepened module is named after it.

## The tool-call gate seam

**Tool call** — one tool invocation the model requested in a turn (`{id, name, args}`).

**Gate** — a policy consulted about a tool call *before* it runs. Each gate is an
adapter satisfying one interface: `evaluate(call, ctx) -> GateOutcome`. A gate
never touches the ledger and never reads the agent directly; it reads `GateContext`.

**Pre-call gate** (this seam) — runs before the call: `workspace`, `guardrail`,
`command_guard`, `egress`, `plan_mode`, `edit_policy`. Six today; the `PreToolUse`
hook becomes a seventh.

**Post-execution veto** (a *different* species, deliberately outside this seam) —
runs *after* the call with its result/diagnostics and can revert: `lsp_enforce`,
`test_gate`. Wider signature (needs result + `pre_content`); do not fold into the
pre-call seam.

**ToolGate** — the deep module at the seam (`src/tool_gate.py`). Holds the ordered
`GATES` tuple and `evaluate(call, ctx, record)`: runs each gate in order, records
each outcome through the `record` sink, applies any `new_args`, and stops at the
first block. **One place every tool call crosses**, replacing the gate sequence
that was copy-pasted across three call sites in `agent.py`.

**GateOutcome** — what a gate returns: `{blocked, block_result, new_args, record}`.
`record` is a `LedgerIntent | None` that fires on **allow or block** (egress logs a
`flag` while allowing; edit_policy records every decision). `new_args` carries a
rewritten payload (egress redact) — applied immutably by the pipeline, not mutated
in place.

**GateContext** — a frozen snapshot of the read-only agent state a gate needs
(`workspace_root`, `protected_paths`, `plan_mode_active`, `plan_path`,
`edit_policy`, `repo_root`, `interactive`, `active_intent`) plus injected
**capability callables** for the effectful/model-dependent bits
(`checkpoint(path) -> sha`, `confirmer`, `classify_edit`). Built per turn. It is
the seam that makes gates testable without an agent.

**LedgerIntent** — the *data* a gate wants recorded (`tool_name, args, result,
success`); `triggered_by` is supplied by the sink, not the gate. The pipeline's
`record` sink turns intents into ledger events — and is the same shape the
planned single `record_event(...)` cognition-event interface will absorb.

### Invariants

- **Gate order is a safety invariant**, defined once in `GATES`, not runtime-tunable:
  hard-safety + cheap-deterministic gates first, the expensive/side-effecting
  `edit_policy` last, the user-defined hook dead last.
- **First block wins.**
- **Recording is uniform**: the pipeline records; gates return data. No gate calls
  the ledger.
- **Inactive gates pass through**: a gate whose precondition is unmet
  (`workspace_root` unset, `command_guard` off, `BYPASS`, plan mode off) returns an
  allow/record-nothing outcome from its own guard clauses.
