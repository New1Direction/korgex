# Changelog

All notable changes to korgex are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.1] — 2026-05-29

### Added
- **`korgex audit` — zero-config verifiable audit of the agent you already run.** Auto-discovers your Claude Code sessions (`~/.claude/projects/**/*.jsonl`), imports the latest into a korg-ledger@v1 chained journal, verifies it, and reports a forensic summary (event count, activity breakdown, tamper-status). No setup, no buy-in — point it at logs you already have and get an instant tamper-evident audit. `import_adapters.discover_claude_code_sessions()` + the `audit` subcommand. The adoption on-ramp for the verifiable-cognition substrate.

## [0.5.0] — 2026-05-29

### Added
- **Cross-vendor import adapters + `korgex import`.** Replay another vendor's session transcript (Claude Code JSONL to start) into a korg-ledger@v1 chained journal — `src/import_adapters.py` parses the transcript, reconstructs causal `triggered_by` links from parent pointers, and hash-chains the events via the shared `ledger_spec`. The output verifies under `korgex verify`. `korgex import claude-code <transcript>` proven on a real 5,475-line session → 2,319 verifiable events. This makes korg the neutral audit substrate *under* any vendor, not another agent beside them.
- **Auto-heal-to-green on a red test gate.** When the in-loop test gate (Gate B) goes red and healing is enabled (`agent.heal_attempts > 0` + `heal_fn`), korgex auto-spawns a healing subagent with the failure log and re-runs the gate, bounded, until red→green or attempts exhausted (`src/self_healing.auto_heal_to_green`). Each attempt and the final `heal.resolved`/`heal.exhausted` verdict is recorded as a hash-chained ledger event, causally linked off the red gate — so a self-repair is itself a verifiable, replayable trail (korgex's analog of thumper's recovery loop). Opt-in; default off.
- **Auditable memory recall in the agent loop.** At task entry korgex recalls its persistent memories, verifies each anchored one against its source baseline, injects only the **fresh** facts into the system prompt, and **withholds stale ones** — recording a `memory_reconcile` (decision="flag") event to the hash-chained ledger for each drift, causally linked off the task prompt (`memory_drift.recall_block` + `KorgexAgent._recall_and_reconcile`). Not "agent memory" (a commodity) but *auditable* memory: every recalled fact is verified-current and every staleness call is on the record. No-op when no memory store exists; never creates one.
- **`korg-ledger@v1` — frozen spec + reference + conformance vectors** (`spec/korg-ledger-v1/`). The tamper-evident hash-chain is extracted out of korgex into a dependency-free reference module (`src/ledger_spec.py`) that korgex now *imports* rather than owns, a normative `SPEC.md` (canonicalization, preimage, chaining, HMAC, verify algorithm), and language-agnostic golden vectors (intact / HMAC / tampered) with **frozen tip hashes** — the cross-implementation oracle for porting the chain into the Rust core. Standalone `conformance.py` harness (exit 0/1) + `_generate_vectors.py` regenerator. This turns "korgex has a hash-chain" into "korg has an open, conformance-tested ledger standard."

### Changed
- Docs: README + `docs/cli-reference.md` now document the v0.4.0 surface — the `korgex verify` and `korgex drift` subcommands, and the `KORGEX_PROVIDER` / `KORG_JOURNAL_PATH` / `KORG_LEDGER_HMAC_KEY` environment variables — plus a new "Verifiable cognition" README section.

## [0.4.0] — 2026-05-29

### Added
- **Tamper-evident hash-chain ledger + `korgex verify`.** Every journal entry is hash-linked (`prev_hash`/`entry_hash`) into a chain; `korgex verify [journal]` walks it and proves the run was not edited, deleted, reordered, or spliced after the fact, localizing any tamper to the offending `seq_id` (exit 0/1, CI-friendly). With `KORGEX_LEDGER_HMAC_KEY` set the chain is tamper-*proof*, not just tamper-evident. The ledger stops being a log you trust and becomes a record you can check — the core of korgex's verifiable-cognition positioning.
- **Ledger-native memory-drift + `korgex drift`.** Memories anchor a sha256 baseline of their source at write time; `korgex drift` scans for drift as an exact content-hash signal, and the keep/refresh/discard reconcile decision is recorded as a `memory_reconcile` event on the tamper-evident chain — an auditable, replayable answer to the trust-hierarchy problem incumbents punt on.
- **`KORGEX_PROVIDER` transport override.** Force the transport (`openai`|`anthropic`) independent of the model id, so Claude/Gemini models can be driven through any OpenAI-compatible gateway (e.g. OpenRouter) on the same provider-agnostic loop.
- `--introspect` emits a `korg:introspect@v1` document describing the running agent.

### Fixed
- **Strict-provider-valid tool schemas.** Array/object tool parameters now preserve their `items`/`properties`, so strict providers (Gemini) accept korgex's tool definitions instead of 400-ing the request. Caught by a real Gemini run, not a fixture.
- **Blob store follows the journal path.** Content-addressed blobs are written beside the journal (tracking `KORG_JOURNAL_PATH`) instead of a cwd-relative `.korg/blobs`, so isolated runs no longer leak into the source checkout. Caught live by the self-coding bench's `no_escape` invariant.

### Changed
- README install link bumped to v0.4.0 / `korgex-0.4.0-py3-none-any.whl`; korg/korgchat/thumper cross-references documented.
- New `docs/self-coding-bench.md`: live reliability data across five third-party models (`glm-5.1`, `qwen3.7-max`, `gemini-3.5-flash`, `claude-sonnet-4.6`, `claude-opus-4.7`) — all 2/2 on the harder bands with zero invariant violations.
- `src/__init__.__version__` now derives from package metadata (was a stale hardcoded `2.0.0`).
- `.gitignore` extended for transient artifacts (`.korg/`, `.hypothesis/`, `ecosystem_audit_*.html`).

## [0.3.2] — 2026-05-27

### Added
- `assistant_text` is plumbed through `record_llm_call` into the journal entry's `result.text` field. Agent transcripts replay with the model's actual reply, not just metadata.

## [0.3.1] — 2026-05-26

### Added
- `payload_refs` flow-through in `KorgBridgeClient`: large blobs (full file reads, full diffs) are content-addressed via `{sha256, size_bytes, label}` triples and kept out of the inline journal.

## [0.3.0] — 2026-05-26

### Added
- **In-process korg-bridge integration.** Every agent loop turn is recorded synchronously into a `.korg/journal.json` via the PyO3 bridge, with HTTP `korg-server` as fallback. `KORGEX_LEDGER=http|bridge|auto` env override.
- Canonical event hashing, bounded write queue, serialized writes (spec §7 compliance).
- Dogfood checklist scripts validating `agent_event_spec.md` §6.

### Fixed
- Critical and High findings from the 2026-05-25 ecosystem audit closed.
- 5 Medium findings closed.
- All Low findings closed.

### Changed
- ROADMAP version sequence corrected; stale progress table removed.
- `comparison.md` rewritten with honest competitive positioning.

## [0.2.2] — 2026-05-24

### Changed
- Hardening pass + full docs overhaul.
- RAG injection format + live SSE stream format captured from Claude Max for parity testing.

## [0.2.1] — 2026-05-24

### Changed
- **Renamed `korgkode` → `korgex`** across the entire codebase, CLI, and packaging.

## [0.2.0] — 2026-05-23

### Added
- **Native MCP (Model Context Protocol) client.** Connects to any MCP server, auto-discovers tools, routes calls back to the originating server.
- **Multi-model routing.** `--mode plan` picks Opus, `--mode execute` picks Sonnet, `--mode debug` picks Haiku, etc.
- **Interactive streaming TUI.** Character-by-character text, diff confirmations on critical edits, spinners, graceful Ctrl+C.
- **Claude Code architectural mirror.** 12 user-facing tools, 4-block system prompt, file-based memory system (4 types, immutable), 10 feature flags with beta headers, session persistence.
- **VS Code sidecar extension.** Refactor / TDD heal / profile / dashboard commands.
- **AST context compression.** Prunes non-focus symbol bodies while preserving signatures and docstrings.
- **Performance profiler.** cProfile injection, pstats parsing, top-N slowest function extraction.
- **Dependency graph impact analysis.** AST-based import mapping, symbol reference tracing, god-node detection.
- **TDD self-healing engine.** Parses tracebacks, queries the LLM for patches, loops until tests pass.
- Multi-agent swarm, AST diff engine, web dashboard, CI/CD daemon + webhook server.
- Strict tool-result pairing + mode-gated tool schemas.
- MCP conformance proof + standalone reusable client package.

## [0.1.0] — KorgKode v1.0

### Added
- Initial release under the old name `korgkode`: 41 tools, cloud sandbox, vision, GitHub API.
