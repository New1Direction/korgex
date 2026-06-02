# Changelog

All notable changes to korgex are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.12.0] — 2026-06-01

The **working-machine** batch — speed, autonomy, and daily-driver ergonomics synthesized from the best of the frontier coding agents, plus a critical fix that revived self-learning.

### Added
- **Cross-vendor prompt caching** (`src/prompt_cache.py`): keeps the stable system prompt + tool definitions warm in the provider's cache so repeated turns skip reprocessing them — faster first token, cheaper calls. One source of truth for each provider's contract: OpenAI/Gemini/Grok/DeepSeek auto-cache (≥1024 tok, no marker), while Claude/Qwen get manual `cache_control` breakpoints (on OpenRouter, a top-level breakpoint also caches the growing history). The native Anthropic SDK path caches the system blocks + tool array. `KORGEX_CACHE_STATS=1` prints a per-turn cache-hit line.
- **`/loop <task>`** (`src/loop_control.py`): grind a task list unattended — seed the work, then auto-continue turn after turn while open tasks remain, with a hard iteration cap (the runaway guard; `KORGEX_LOOP_MAX`) and Ctrl-C to stop.
- **`@file` mentions** (`src/mentions.py`): `@path` in a prompt inlines that file's contents (email/word-guarded, size-capped, dirs skipped) — the original instruction is preserved and bodies are appended in a fenced section.
- **`!command` shell passthrough**: run a shell command (`!git status`, `!pytest -q`) straight from the REPL, in the project root.
- **Post-turn change summary**: after a turn, `✎ changed N file(s): path (+a -b)` computed from the rewind snapshots vs disk — tied to `/rewind`.
- **Project-rules hierarchy** (`src/project_rules.py`): merges `~/.korgex/AGENTS.md` + the git-bounded directory chain (monorepo root → package, never above the repo) + `.korgex/rules/*.md`, least-specific first — so korgex respects house style across real repos, not just the root file.
- **Self-learning skill curator** (`src/skill_curator.py`): an LLM groups the agent-learned skills by intent and consolidates near-duplicates into one, deleting the redundant ones. Touches only `trust: agent` skills (user/built-in are never merged or deleted). `/skills curate` runs it on demand; a throttled background pass runs as the library grows.
- **`korgex init`** (`src/project_init.py`): scaffold a starter `AGENTS.md` for the current repo — detects the stack and test/build commands; never clobbers an existing one.
- **Typed `subagent.result` ledger node**: each delegation records a first-class, queryable outcome naming the child's root seq, so multi-agent runs are coherent and rewindable per child.

### Changed
- **System prompt sharpened** to frontier-agent posture: output economy on both ends (no preamble *and* no postamble), match-the-codebase discipline (mimic style, verify deps exist, comment only the "why", no unrequested refactors), a direct non-sycophantic tone that pushes back, and decisive autonomy with a scope guard.
- **Live token streaming + parallel MCP connect**: replies stream as generated; configured MCP servers connect concurrently at startup (5 servers + 31 tools in ~0.9s, was the sum).

### Fixed
- **Self-learning was silently dead in production**: `Repl.self.repo_root` was never assigned, so `/skills`, `@`-mentions, and skill learning hit an `AttributeError` that their `try/except` swallowed — skills never actually learned. Now set to the launch dir.
- **The live task list never reached OpenAI-compatible models**: the anti-drift task steering drove the UI and the Anthropic path but was dropped on the OpenAI path. Now delivered as a trailing message (cache-safe).

## [0.11.0] — 2026-06-01

The **terminal-native conversational agent** batch — korgex becomes a cross-vendor Claude Code competitor you live inside, plus a fleet of frontier-agent capabilities.

### Added — the CLI experience
- **Interactive REPL** (`src/repl.py`): bare `korgex` on a TTY drops into a streaming, multi-turn session. Bottom-pinned input via `prompt_toolkit` (input stays on the last row, output scrolls above in preserved scrollback), in-memory history, slash commands (`/model`, `/plan`, `/clear`, `/help`, `/exit`). Non-TTY still prints help so scripts/CI never hang.
- **`korgex setup`** (`src/setup_wizard.py`) + **config** (`src/config.py`): connect any provider (OpenRouter / Anthropic / OpenAI / Ollama) — keys + a default model saved to `~/.korgex/config.json` (0o600). The agent now resolves its key + base_url from config, not just env.
- **Priced model selector** (`src/model_selector.py`): `/model` shows a numbered, cost-labeled menu; defaults to a mid/cheap tier (no surprise Opus billing). Free-text model ids still work.
- **Premium startup**: a red-gradient `KORGEX` wordmark, a bordered welcome panel (gradient portal + model/providers/MCP/skills/quick-tips/summary), and a streaming-style bordered input box (`src/banner.py`, `src/tui_app.py`).
- **Clean block-rendered output** (`src/render.py`): per-role accent-bar blocks (`▎ you` / `▎ korgex`), compact `◆ verb target` tool lines, head/tail truncation, optional markdown re-render (`$KORGEX_MARKDOWN=1`), and a thinking spinner that overwrites in place.

### Added — agent capabilities
- **Tiered tool exposure + real `ToolSearch`** (`src/tool_search.py`): a BM25-flavoured index over deferred tools; MCP/plugin tools are discovered on demand instead of dumped into every request.
- **Classifier permission mode** (`src/policy_classifier.py`): an `auto` edit-policy tier where a cheap model judges each action against your natural-language rules (allow / soft-deny / hard-deny), with the deterministic hard-block floor underneath.
- **Plan mode** (`src/plan_mode.py`): read-only until you approve the agent's plan; only writes to the plan file until then.
- **Model-authored compaction** (`src/compaction.py`): summarize-and-continue when a long run nears the context window — ledger-logged, fail-safe.
- **File-defined skills** (`src/skills.py`): `SKILL.md` registry with progressive disclosure (a compact index in the prompt, body loaded on demand) and trust tiers.
- **Loop safety rails** (`src/loop_guard.py`) + **typed stall classifier** (`src/stall_classifier.py`): kill infinite retries, nudge narrate-not-act, and flag false-completion.
- **Provenance-verified agent bus** (`src/bus.py`): per-message Ed25519 signatures, re-resolved per message — catches tampering and impersonation on the coordination channel.
- **MCP elicitation + sampling** (`src/mcp_reverse.py`): servers can ask the user a question or borrow the client's LLM.

## [0.10.0] — 2026-05-31

The agent-economy batch: agents can now **coordinate, sign, deal, and get paid** — and "who did this" becomes a signature, not a claim. Every step is a pure function of the chain, verifiable in any browser.

### Added
- **Verifiable agent message bus** (`src/bus.py`). Multi-agent coordination where every message and every read-receipt is its own hash-chained korg-ledger@v1 event. `BusSend` / `BusInbox` agent tools with auto-delivery, so agents coordinate hands-free and the whole exchange replays and verifies from the record alone.
- **Ed25519-over-tip signing** (`src/signing.py`). An agent's identity is its public key; it signs the chain's tip, so "who attests to this history" is a verifiable signature carried in a sidecar checkpoint — kept off the hashed chain, so the cross-language conformance vectors stay byte-identical. Anyone verifies in-browser (WebCrypto) with zero trust in korg.
- **Sealed-envelope primitive** (`src/sealed_envelope.py`). Commit-reveal: seal a value before an outcome, reveal it after; the reveal recomputes byte-for-byte identically in Python, Rust, and the browser. The shared foundation under every receipt.
- **Anchored-tip verification.** `ledger_spec.verify_chain(expected_tip=…)` closes the unkeyed-regeneration hole — a forger who re-links and re-hashes an entire chain still can't make its tip equal an externally published one (that would be a SHA-256 second-preimage).
- **Sealed Deliverable Receipt + x402 escrow** (`src/contract.py`). Two agents agree (offer → accept); the deliverer **seals** its work before a deadline and **reveals** it after; an acceptance test is recorded; and the verdict — SETTLED / DEFAULTED / FRAUD / FAILED — is a pure function of the chain. The seal is **signed** (provable authorship) and the money leg rides **x402**: release to the seller on provable delivery, refund the buyer on default or fraud. korg decides who's owed; x402 settles.
- **Proof-of-custody** (`src/custody.py`). Seal any file and prove later that it's byte-for-byte unaltered — korg's first non-agent receipt, and the primitive under the consumer "Sealed" tool.
- **Demos** (`demos/`). A hands-free two-agent arena (korgex vs codex play a full match over the real bus; the board reconstructs from the journal alone) and a reproducible, self-verifying generator for the sealed-deliverable receipt page.

## [0.9.0] — 2026-05-30

A verifiability + code-intelligence batch — and the moat's central claim is now **proven across languages**.

### Added
- **Secret sanitization at the ledger boundary** (`src/sanitize.py`). Provider keys, tokens, PEM private-key blocks, and secret-named fields are redacted from tool args/results at **every** write path (local / bridge / http / import) **before** blob-extraction and hashing — so the now-shareable proofs (`audit --html`) can never leak a credential, and the chain still verifies. The conversation the model sees is untouched.
- **Language Server Protocol client + `korgex diag`** (`src/lsp.py`). Content-Length JSON-RPC framing, initialize/didOpen/diagnostics; `diagnostics(path)` drives pyright / typescript-language-server / rust-analyzer / gopls best-effort and time-guarded. `korgex diag <file>` reports errors/types so the agent can check an edit instead of editing blind.
- **Auto-diagnostics-on-edit + opt-in enforcement.** `$KORGEX_LSP_DIAGNOSTICS=1` folds a language server's findings into the edit's result (the agent sees what it just broke, mid-loop) and records an `lsp.diagnostics` event. `$KORGEX_LSP_ENFORCE=1` upgrades that to a hard veto — a severity-1 edit is reverted and refused as a verifiable policy event. Both default-off.
- **Non-BMP conformance vector + bridge round-trip** (`spec/korg-ledger-v1/vectors/nonbmp-intact.jsonl`). Proves byte-for-byte canonicalization across Python / Rust / JS on emoji / CJK / astral-plane text — the surrogate-pair path that no vector previously exercised. Determinism **held**; no divergence found. Plus a test that re-verifies a written-to-disk journal through `ledger_spec`.
- **`korg:introspect@v1` cross-language contract test** so the Python and TypeScript introspect surfaces fail in lockstep on schema drift.
- **Shared-event-shape contract + interleaved one-journal test** — korgex, korgchat, and thumper events verified end-to-end in a single chain.
- **Cross-vendor end-to-end flow proof + runbook** (Claude Code → import → verify → `audit --html`; witness tap → verify), with the report's embedded JS verifier reproducing the Python tip byte-for-byte.

## [0.8.0] — 2026-05-30

A batch of agent-architecture upgrades, each recreated generically and each tied
back to the tamper-evident ledger (the moat).

### Added
- **`witness` — tap or import any tool-dispatch into a verifiable korg-ledger chain.** A self-contained, stdlib-only `tap(handle_tool)` wrapper (`integrations/witness/`) turns any tool-running loop — MCP server, agent, CLI router — into a tamper-evident, replayable record with two lines; opt-in via `$KORG_TAP_JOURNAL`, fail-safe (a ledger error can never break a tool call), resumes across restarts. Plus `korgex import witness <journal>` for an existing tool-event log → `korgex verify` / `audit --html`.
- **Edit-approval policy + checkpoint-before-mutation + ledger trail** (`src/edit_policy.py`). Before any file-mutating tool runs, korgex consults a policy (`ASK`/`WORKSPACE`/`SESSION`; `$KORGEX_EDIT_POLICY`), **hard-blocks** `.git`/`.ssh`/`.gnupg`, **always-asks** sensitive paths (`.env*`, `id_*`, `*.pem`/`*.key`, `credentials*`, `.npmrc`/`.pypirc`, `.aws`/`.kube`), and fails **closed** (timeout/error → deny). An approved edit in an isolated worktree is **checkpointed-before-mutation** (revertable; never commits to the user's working branch), and **every decision is recorded to the ledger** — a verifiable trail of exactly what the agent was permitted to touch.
- **`korgex trajectory` — verifiable training trajectories** (`src/trajectory.py`). Export a run as a normalized (ShareGPT) training trajectory **stamped with its source's provenance**, so the data carries proof it came from an unaltered run. A tampered source → `verified: false` (a built-in poisoning defense). Export is **append-only** — trajectories accumulate into a flywheel of verifiable runs.
- **In-process plugin registry** (`src/plugins.py`). Complements the shell command-hooks with low-latency Python observers on the agent lifecycle (`on_user_prompt` / `pre_tool` / `post_tool` / `on_stop`), generalizing the `witness` tap into a registerable surface. Fail-safe (a raising plugin is isolated) and a zero-overhead no-op when empty.

## [0.7.0] — 2026-05-29

### Added
- **`korgex audit --html` — a self-verifying, shareable audit report.** Turns `korgex audit` from terminal text into a single self-contained HTML file that **re-verifies the korg-ledger@v1 hash chain in the recipient's own browser** — no need to trust the tool that produced it. It carries a plain-English narrative of what the agent did, a visual of the chain, and a live **tamper test** that edits one recorded event and shows the chain break, localized by `seq` — turning tamper-evidence from a claim into something you can feel. No network calls (an audit artifact must not phone home). The embedded verifier (`src/assets/korg_verify.js`) is the same reference algorithm as the Python and Rust cores, proven against the frozen conformance vectors **and** on real multi-thousand-event sessions (the in-browser recomputed tip matches the Python journal, zero false positives). The zero-buy-in adoption on-ramp: point it at logs you already have, get a proof you can hand to anyone.

## [0.6.3] — 2026-05-29

### Added
- **Listed in the official MCP Registry** (`io.github.New1Direction/korg-ledger`). The registry validates package ownership by requiring the marker `mcp-name: io.github.New1Direction/korg-ledger` in the PyPI package README — added here (a released version's README can't be edited in place, hence the patch bump). `server.json` migrated to the current `2025-12-11` schema with a ≤100-char description. `korg-ledger` is now discoverable from any MCP host via the registry, pointing at `pypi:korgex`.

## [0.6.2] — 2026-05-29

### Fixed
- **`korgex` crashed on a clean install (no `requests`) — and is now on PyPI.** `src/korg_ledger.py` did a top-level `import requests` (used by the HTTP korg-server transport), but `requests` was never a declared dependency. Dev/CI envs have it transitively (twine pulls it in), so every local run masked the failure — but a fresh `pip install` with `requests` absent died with `ModuleNotFoundError` before the CLI could even parse args. This is the **exact same class** of bug as the v0.6.1 PyYAML fix. Found while verifying the first PyPI publish in a clean venv. Fix: declare `requests`.

### Added
- **Regression test for the whole undeclared-dependency class** (`tests/test_no_undeclared_module_imports.py`). AST-scans `src/` for *bare module-level* third-party imports and asserts each is provided by a distribution declared in `pyproject` — resolving import-name→dist-name via `top_level.txt` (so `yaml`→`PyYAML`), skipping stdlib (via `find_spec` origin, no `sys.stdlib_module_names`) and lazy in-function imports. Would have caught **both** PyYAML and `requests`. Dependency-light and 3.9-compatible so it runs in the local suite, not just CI.
- **korgex published to PyPI** — `pip install korgex` is now the primary install path (0.6.2 is the first version on PyPI and the first verified to import cleanly from a fresh install with only its declared dependencies).

## [0.6.1] — 2026-05-29

### Fixed
- **`korgex <prompt>` crashed on a clean install (no PyYAML).** The idea-#5 memory-recall wiring made every `run_task` import `src.memory` → `yaml`, but PyYAML was never a declared dependency — fatal (`ModuleNotFoundError`) on any environment without it (regression shipped in v0.5.0–v0.6.0; **caught by the new Gate F CI run, which the local suite masked** because PyYAML was present locally). Fix: declare `pyyaml` as a dependency, and make `_recall_and_reconcile` fail-safe so the memory subsystem can never crash the agent loop (degrades to no recall).

### Added
- **Gate F — live-LLM self-coding bench in CI** (`.github/workflows/self-coding-bench.yml`): a manual-dispatch workflow that runs korgex-bench end-to-end against a live model (via the `KORGEX_API_KEY` secret), asserts the three zero-invariants (no_escape / no_green_on_red / durable_ledger), then verifies the bench journal is intact. The reproducible "trust number" gate — no-op (green) when the secret is absent. Plus a more discriminating seed task with a precise behavioral oracle (`korgex import <unknown-vendor>` should exit 2), not just "suite green".

## [0.6.0] — 2026-05-29

### Added
- **korg-ledger MCP server (`korgex mcp-server`).** Exposes the verifiable-cognition substrate over MCP (JSON-RPC 2.0 / stdio, dependency-free) so any MCP host — Claude Desktop, Cursor, … — can call `korg_verify` (prove a journal is tamper-evident-intact), `korg_audit` (audit the host agent's own Claude Code logs), and `korg_import` (import a vendor transcript into a chained ledger). "Be the substrate, not an app" at the protocol layer — the governance/audit gap the MCP roadmap names. Wire it with `{"mcpServers":{"korg-ledger":{"command":"korgex","args":["mcp-server"]}}}`.

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
- RAG injection format + live SSE stream format (internal notes) for parity testing.

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
