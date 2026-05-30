# korg-ledger@v1 — SHARED-EVENT-SHAPE contract

**Status:** v1 · **Layers on:** [`SPEC.md`](./SPEC.md) (`korg-ledger@v1`) · **Reference impls:** korgex `src/korg_ledger.py`, korgchat `src/korgchat/chat.py`, thumper `src/ledger/journal.rs`

[`SPEC.md`](./SPEC.md) defines the *chain*: how a sequence of JSON events is
hashed, linked, and verified byte-for-byte. It deliberately says nothing about
what the events *mean* — `prev_hash` and `entry_hash` are the only reserved
fields; everything else is "opaque application payload".

This document fills that gap. It is the **shared event shape** every korg
cognition producer agrees to, so that one journal file can hold events from
korgex (agent tool calls), korgchat (chat turns), and thumper (bun runs / heal
sessions) **interleaved**, and a single verifier reads the whole thing as one
hash-chained, causally-sound audit trail. The wedge — #3, ONE shared ledger —
is exactly this: a single auditable journal as the sink for ALL cognition.

> If SPEC.md is "the bytes are intact", this contract is "and here is what the
> bytes say happened, in a vocabulary all three subsystems share".

## 1. The common event envelope

On top of the two reserved chain fields (`prev_hash`, `entry_hash`), every
event written by a conformant producer carries these fields. They are the
columns a cross-subsystem reader can rely on:

| Field | Type | Required | Meaning |
|---|---|:--:|---|
| `schema_version` | string | ✓ | event-shape version. Currently `"1.0"`. |
| `seq_id` | integer ≥ 1 | ✓ | monotonic, per-journal. Assigned by the writer; never reused. |
| `source_agent` | string | ✓ | who produced this event (§2). |
| `tool_name` | string | ✓ | what happened (§3). |
| `args` | object | ✓ | the inputs / request side of the event. |
| `result` | object | ✓ | the outputs / response side. |
| `success` | bool | ✓ | did it succeed. |
| `duration_ms` | integer ≥ 0 | ✓ | wall-clock cost, `0` if instantaneous/unknown. |
| `triggered_by` | integer | — | `seq_id` of the **strictly earlier** event that caused this one. Absent ⇒ this event is a causal root. |
| `payload_refs` | array | — | content-ref index for blobbed `args`/`result` (§4). |

Field-level rules:

- **Reserved fields are `prev_hash` / `entry_hash` only.** Everything above is
  ordinary payload and is part of the hash preimage (SPEC.md §3) — so changing
  any of it breaks the chain at that `seq_id`. That is the point.
- **No floats, anywhere.** v1 canonicalization is integers/strings/bools/null/
  objects/arrays only (SPEC.md §7). Durations are integer milliseconds.
- **`triggered_by` must reference a strictly-earlier `seq_id`** so the journal
  is a backward-pointing DAG and rewind-by-truncation stays sound
  (SPEC.md §5, `verify_dag`). Cross-subsystem causality is allowed and
  encouraged: a korgchat turn that shells out to a thumper run SHOULD point the
  run's root event's `triggered_by` at the turn's `llm_inference` seq.
- **Optional fields are omitted, not null.** A root event has no `triggered_by`
  key at all (so its preimage — and hash — matches a producer that never knew
  about causality). This is load-bearing for cross-impl byte-identity.

## 2. `source_agent` — who

A namespaced actor identity. The prefix is the kind; the suffix identifies the
instance. This is how an auditor tells "korgex did X" from "korgchat did Y" in
one interleaved file.

| Prefix | Meaning | Examples in use |
|---|---|---|
| `agent:<name>@<version>` | an autonomous agent runtime | `agent:korgex@0.3.2`, `agent:korgchat@0.5.3`, `agent:korgchat-summarizer` |
| `human:<id>` | a human override / direct input | `human:korgchat-user`, `human:dusk` |
| `korg:<component>` | korg-internal machinery | `korg:registry` |
| `mcp:<server>` | an MCP server client | `mcp:filesystem` |
| `<bareword>` | a standalone tool with one identity | `thumper` |

`thumper` writes the bare string `thumper` (it predates the namespacing and is a
single-identity producer); that is grandfathered and explicitly allowed. New
producers SHOULD use a namespaced form.

## 3. `tool_name` — what

`tool_name` is a dotted lowercase verb. Producers share these families so a
reader can filter "all inference", "all heal activity", etc. across subsystems:

### 3.1 Cognition primitives (any agent producer)

| `tool_name` | `args` | `result` | Emitted by |
|---|---|---|---|
| `user_prompt` | `{prompt}` | `{}` | korgex, korgchat |
| `llm_inference` | `{model, prompt_tokens}` | `{completion_tokens, text?}` | korgex, korgchat |
| `summary` | `{...}` | `{digest}` | korgchat |

`llm_inference` causality is special (korgex `agent_event_spec` §2a): round *N*'s
`llm_inference.triggered_by` points at round *(N-1)*'s `llm_inference` seq, **not**
at the most recent tool call. Tool calls within a round are siblings under that
round's inference.

### 3.2 Tool calls (the agent decision boundary)

A `tool_name` that is the literal name of an invoked tool — `Edit`, `Bash`,
`Read`, `Write`, etc. One event per completed call. `args` is the tool input,
`result` is its output, `success`/`duration_ms` describe the call.
`triggered_by` is the `seq_id` of the `llm_inference` that requested it.

korgchat additionally emits audit siblings around a call:
`tool_schema_snapshot` (the frozen `input_schema` the model was shown) and
`tool_validation` (`{valid, violations}` against that snapshot).

### 3.3 Execution & recovery (thumper)

| `tool_name` | `args` | `result` | When |
|---|---|---|---|
| `run.exec` | `{operation, verb, argv}` | `{exit_code}` | a normal bun run completes (native path). A causal root. |
| `heal.error` | `{command, error_excerpt}` | `{}` | a failure is intercepted. |
| `heal.repair` | `{error_type, file, ...}` | `{strategy}` | a repair is attempted (`triggered_by` = the `heal.error`). |
| `heal.exit` | `{command}` | `{healed}` | the heal session ends (`triggered_by` = last repair/error). |

`run.exec` is the deliverable that closes the wedge gap: thumper's **normal**
execution path now lands in the same chained journal as heal, so the journal is
the sink for *all* of thumper's cognition, not only the failure path.

## 4. `payload_refs` — large content

When an `args` or `result` value serializes to more than 1 KiB, the producer MAY
replace it with a content-ref sentinel `{"_ref": "sha256:<hex>", "size_bytes": n}`
and record `{"sha256", "size_bytes", "label"}` in `payload_refs`. The blob bytes
live in a content-addressed store next to the journal (`<journal_dir>/blobs/`).
The hash is computed over the blob's canonical bytes (korgex `_canonical_bytes`),
so two producers blobbing identical content produce the same ref. Secrets MUST be
redacted **before** blob extraction and hashing — the journal and its blob store
are shareable proofs and must never carry a credential.

## 5. One journal, many producers

The reason the envelope and the `triggered_by` rules are this strict: the target
state is a **single** journal file that all three subsystems append to, in any
interleaving, and which verifies as one chain.

- **Same path.** All producers resolve the journal from the same env vars:
  `KORG_JOURNAL_PATH` is the shared key; thumper also accepts
  `THUMPER_JOURNAL_PATH` (which wins for thumper only). Point them all at one
  file and the events interleave into one chain.
- **Same canonicalization.** Python (korgex/korgchat via `korg_bridge` / the
  reference module) and Rust (thumper `src/ledger/chain.rs`) produce
  byte-identical preimages — proven by the frozen conformance vectors
  ([`conformance.json`](./conformance.json)). That byte-identity is what lets a
  thumper-written line and a korgchat-written line sit in the same chain and
  both re-hash correctly under one `verify_chain`.
- **Chain continuity.** Every producer recovers `(max seq_id, chain head hash)`
  from the existing file on open and continues — it does not reset to GENESIS.
  So appends from different producers extend ONE chain rather than forking.
- **Serialized writes.** Concurrent producers must serialize their appends (one
  writer advances `seq_id` and `prev_hash` at a time) or the chain forks. korgex
  wraps this in `ThreadSafeLedger`; in-process the `korg_bridge` registry holds
  the mutex; across processes, file-level coordination is the producer's
  responsibility.

A reader verifies the whole interleaved file with the ordinary
`verify_dag(events) + verify_chain(events, key)` from SPEC.md §5 — no
producer-specific logic. If every producer obeys this contract, the result is a
single tamper-evident, causally-sound record of everything korgex, korgchat, and
thumper did, judged by one oracle.

## 6. Conformance

A producer is **shape-conformant** iff every event it writes:

1. carries all the §1 required fields with the stated types;
2. uses a `source_agent` of one of the §2 forms;
3. uses a `tool_name` from §3 (or a tool-call name per §3.2);
4. omits (does not null) `triggered_by` for roots, and otherwise references a
   strictly-earlier `seq_id`;
5. emits no floats; and
6. is chain-conformant per [`SPEC.md`](./SPEC.md) (verifies against the frozen
   vectors).

The cross-subsystem integration test
`korgex/tests/test_shared_journal_interleaved.py` is the executable oracle for
this document: it writes a korgex tool call, a korgchat turn, and a thumper run
into ONE journal and asserts the whole interleaved chain + DAG verify.
