# Changelog

All notable changes to korgex are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- README refreshed: v0.3.2 install link, korg/korgchat/thumper cross-references documented.
- `.gitignore` extended for transient artifacts (`.hypothesis/`, `ecosystem_audit_*.html`).

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
