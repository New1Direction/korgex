# Changelog

All notable changes to korgex are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Skills: install / search / adopt from the open catalog.** korgex skills are the Anthropic Agent-Skills format (`SKILL.md`) — the *same* format published across public GitHub and indexed by [skills.sh](https://skills.sh), so the whole ecosystem is consumable with no adapter. New `src/skill_install.py` + CLI:
  - `korgex skills install <ref>` — install from a local dir, a git URL, or an `owner/repo[@skill]` skills.sh shorthand (which resolves to the GitHub repo). Skills land as `trust: installed` with a `source:` provenance stamp in their frontmatter.
  - `korgex skills search <query>` — search the skills.sh catalog (public repos + install counts); install a hit with `korgex skills install <source>@<skillId>`.
  - `korgex skills adopt <dir>` — pull skills already on disk (e.g. `~/.claude/skills`) into korgex's store, no re-download. Tolerates the legacy lowercase `skill.md` marker.

  Network (git clone, the skills.sh HTTP) is injected, so the logic is fully unit-tested offline. korgex keeps its edge (trust tiers + self-learning curator + verifiable ledger); this adds discovery/distribution on top.

### Fixed
- **`korgex skills <subcommand>` was rejected by the CLI parser.** The `skills` subparser had no positional argument, so `korgex skills log` (and now install/search/adopt) errored with "unrecognized arguments" before the handler ran. Added the positional so the subcommands dispatch.

## [0.26.0] — 2026-06-04

### Added
- **ACP: inline edit-preview diffs on tool-call cards.** A `tool_call` for an `Edit`/`Write` now carries an ACP `diff` content block (`{path, oldText, newText}`) built from the call args alone (no filesystem read — `Edit` shows its old→new fragments, `Write` shows the new content), so the editor renders the proposed change as an inline diff on the activity card, right next to the permission prompt.
- **ACP: editor-mediated edit approvals (`session/request_permission`).** When korgex can't auto-allow an edit — a sensitive path, or `KORGEX_EDIT_POLICY=ask` — it now asks the *editor* to approve instead of its own terminal prompt: a blocking agent→client `session/request_permission` request offering **Allow / Allow (don't ask again) / Reject**. "Don't ask again" relaxes the policy for the rest of the session; reject/cancel fails safe to denied. The stdio transport gained a blocking outbound-request path that services intervening messages (e.g. a `session/cancel`) while it waits, so a mid-turn prompt can't deadlock the loop. Wired into korgex's existing `_edit_confirmer` seam — no core-loop change — and unit-tested end-to-end over a `StringIO` transport.
- **ACP: live streaming + tool-call activity (`korgex acp` in an editor).** The Agent Client Protocol agent (0.24.0) was correct but thin — it sent one `agent_message_chunk` *after* the whole turn, with no visible tool activity. It now streams as the loop runs: each tool fires a `tool_call` / `tool_call_update` (with an ACP `ToolKind` and a human title) and each round's narration fires an `agent_message_chunk`, so an editor like **Zed** shows read/edit/run cards and live reply text instead of one opaque blob. Wired through korgex's existing plugin lifecycle (new `on_assistant_text` hook + `pre_tool`/`post_tool`), so the core agent loop is untouched and the bridge stays fully unit-tested (`src/acp.py::register_streaming` / `make_live_run_turn`). Also: `embeddedContext` capability is now advertised true and `session/prompt` accepts `resource_link` + embedded `resource` blocks (editor `@file` mentions / pasted context), not just plain text. README documents the Zed `agent_servers` config. Remaining follow-ups (after this release's permissions + diffs): mid-turn `session/cancel` (needs a concurrent stdin reader), `session/load`, and wiring the client's forwarded `mcpServers`.

## [0.25.0] — 2026-06-04

### Added
- **Verifiable prompt-cache hits + honest cache-aware cost.** Each turn's prompt-cache usage was captured but dead-ended at a behind-a-flag print — it never reached the ledger or the cost model. Now the disjoint cache breakdown (`cache_read_tokens` / `cache_creation_tokens` / `uncached_input_tokens`) is recorded on every `llm_inference` event, so a cache hit is **provable from the tamper-evident journal** (`korgex verify`), not just a console line. `src/cost.py` prices that breakdown correctly: cached reads at their real discounted rate (Anthropic ~90% off, OpenAI ~50% off) and Anthropic cache writes at their ~25% surcharge — fixing a two-sided error where the old `prompt_tokens × rate` estimate **undercounted Anthropic** (it ignored cache tokens entirely) and **overcounted OpenAI** (it billed the cached subset at full rate). `korgex cost` now also surfaces cache reads and dollars saved.

### Fixed
- **Cache-token counts were redacted from the ledger.** The secret-shape sanitizer treated the new `cache_*_tokens` keys (they contain "token") as credentials and wrote `[REDACTED]`, which would have erased the cache breakdown before it hit disk. Added them to the token-count allowlist alongside `prompt_tokens`/`completion_tokens` — counts, never secrets.

## [0.24.0] — 2026-06-04

### Added
- **ACP (Agent Client Protocol) agent — `korgex acp`.** korgex now speaks the open [Agent Client Protocol](https://agentclientprotocol.com) as an *agent* over stdio (JSON-RPC 2.0), so ACP editors/clients (Zed et al.) can drive it. `src/acp.py`: the `initialize` capability handshake, `session/new`, `session/prompt` (bridged to korgex's agent loop, streaming `agent_message_chunk` updates + a stop reason), and `session/cancel`. Implemented **clean-room from the open spec**; the `AcpAgent` dispatcher is transport-agnostic and dependency-injected, so the protocol layer is fully unit-tested (the live-agent bridge redirects the agent's stdout to stderr so it can't corrupt the JSON-RPC channel). End-to-end against a real ACP client is the remaining validation. Fits the cross-vendor positioning: one verifiable agent, drivable from any ACP editor.

### Changed
- **Maintainability: extracted resolution helpers out of `agent.py`.** The pure provider/model/mode/subagent *resolution* functions (`_looks_anthropic`, `_oauth_provider_for`, `_oauth_token_and_base`, `subagent_tools`, `_resolve_params`, `_resolve_model` + their constants) moved from the agent loop into a focused, independently-testable `src/agent_resolve.py` (re-imported by `agent.py`, so behavior is byte-for-byte unchanged — all 1432 tests pass). `agent.py`: 2838 → 2704 lines. A first safe slice of a larger decomposition of the core loop.

## [0.23.0] — 2026-06-04

### Added
- **Verifiable best-of-N selection.** `run_best_of_n` (korgantic) — run the same task N times in isolated worktrees, gate each, pick a passing/auto-mergeable winner — now optionally records its run to the ledger: a root, a `best_of_n.attempt` event per candidate (gate-pass + auto-mergeability), and a `best_of_n.selected` verdict naming the winner and *why* (auto-mergeable · needs-review · none-passed). With that trail, `korgex why`/`verify` can **prove** the N candidates ran under the test gate and the pick is honest — korgex's best-of-N becomes auditable, not just returned in a dict. Opt-in via a `ledger=` arg; without it, behavior is byte-for-byte unchanged.
- **MMR re-ranking for recall (diversity).** `recall.mmr_rerank` + an opt-in `diversify=True` on `build_lean_context` (now used by `korgex recall`) re-rank matches for relevance AND novelty (Maximal Marginal Relevance), so a cluster of near-duplicate events doesn't eat the lean-context budget — the chosen events are still rendered chronologically. Pure/LLM-free (token-Jaccard similarity); default off, in which case selection stays purely chronological.
- **Self-hosted inference recipe — `docs/self-hosted-inference.md`.** The fast-*and*-cheap recipe written up end-to-end: a low-active-param MoE (e.g. a `Qwen3-30B-A3B`-class model) → quantize (AWQ/FP8) → vLLM with speculative decoding → `korgex providers` + lean context. Ties the provider preset, the custom `KORGEX_API_URL` endpoint, lean-context, and FTS recall into one "run your own fast model" guide.

## [0.22.0] — 2026-06-04

### Added
- **FTS5 BM25 recall — sharper, dependency-free retrieval.** A new `recall.search(mode="fts")` (used by `korgex recall`) indexes the ledger events with SQLite **FTS5** and ranks by **BM25** instead of the default substring scorer's raw term-occurrence + strict all-terms-must-appear (which could return nothing for a multi-term query). Partial matches are allowed and path/identifier tokens are split, so e.g. searching `auth middleware` also surfaces a `test_auth.py` run. Runs on the Python **stdlib** (FTS5 is built into `sqlite3`) — no new dependency, no network — and falls back to substring if FTS5 is ever absent. (A persistent `sqlite-vec`/vec0 semantic store needs a Python whose `sqlite3` can load extensions, which macOS system Python can't; semantic ranking stays covered by the existing optional fastembed cosine path, `mode="semantic"`.)
- **Single-message repetition rail (`loop_guard`).** A pure, LLM-free guard that catches degenerate repetition *within a single model response* — the same line, or the same multi-line block, repeated over and over (a stuck loop distinct from `RepeatGuard`'s repeated *tool calls* across turns). Uses a P×K score (`pattern_length × repetitions`) so short patterns need many reps and long ones only a few; blank-line runs never count. When it fires in the loop, korgex records a `loop_guard.repetition` ledger event and nudges the model to break out — capped, so the nudge can't loop itself.

## [0.21.0] — 2026-06-04

### Added
- **`korgex receipt share <file> --publish` — hosted shareable receipts.** Publishes a receipt's self-verifying page into a configured static-site checkout (`KORGEX_SHARE_PAGES_REPO`) under `r/<id>.html` and git-pushes it, returning a real public URL like `https://yvaehkorg.lol/r/<id>.html`. Served as real HTML it unfurls as a social card *and* re-verifies the hash chain in the recipient's browser; the id is the receipt's chain tip (content-addressed → same receipt = same stable URL). Zero new infrastructure — reuses a static host you already have and your existing git auth. Closes the loop: run → publish → share a link a stranger verifies with zero trust.

## [0.20.0] — 2026-06-04

Three features that turn the verifiable-cognition moat into distribution and self-hosting.

### Added
- **`korgex receipt share <file>` — a shareable, self-verifying proof page.** Renders a receipt as one self-contained HTML page that **unfurls as a social card** when posted (Open Graph + Twitter `summary_large_image`, the claim as the title) and **re-verifies the hash chain in the recipient's browser** — reusing the conformance-tested in-browser verifier, no second implementation. It surfaces the signer and offers a "verify it yourself, another way" panel: a button to download the exact receipt JSON, plus the `korgex receipt verify` / `korg-verify` commands. Host it where real HTML is served (e.g. GitHub Pages) and the link unfurls with a proof card; `KORGEX_SHARE_BASE_URL` / `KORGEX_SHARE_OG_IMAGE` override `og:url` / `og:image`. The viral loop for provable agent work: run → share a link → a stranger verifies it with zero trust → shares.
- **Causal retrieval — lean context that follows the ledger DAG, not just text.** Retrieval now walks the `triggered_by` edges korgex already records: `recall.expand_causal` pulls a matched event's **cause** (the prompt that triggered an action — the "why") and/or its **effects** (the actions a prompt triggered — the "what happened"). `lean_context.build_lean_context(..., causal=...)` makes it opt-in (off by default → plain text-relevance retrieval is unchanged), and the live loop uses `causal="causes"` so each matched action brings the prompt that explains it **without dragging in a broad prompt's unrelated siblings**. Zero new dependencies — the causal half of retrieval that flat text search over a non-causal store can't do. (Semantic ranking already existed, optional/lazy via fastembed.)
- **`korgex providers` — point korgex at your own endpoint in one command.** `korgex providers add <name> --url <base> --model <model> [--type openai] [--key K | --key-env VAR]`, then `korgex providers use <name>` aims the whole agent at a self-hosted OpenAI-compatible box (vLLM, llama.cpp, a gateway) — a named layer over the existing `KORGEX_API_URL` routing, no env-var juggling. `list` / `remove` round it out. Named providers (`name → endpoint → model`) plus an active selection live in `~/.korgex/config.json`; resolution prefers the active named provider for its model — the way two OpenAI-compatible endpoints are told apart. Keyless servers need no key.

### Fixed
- **A `claude-*` model id is never rerouted.** Previously, with exactly one non-Anthropic provider configured (or a custom `KORGEX_API_URL` set), an explicit `claude-…` request could be hijacked to that endpoint. Claude is unambiguous and now always resolves to Anthropic, regardless of configured presets or custom endpoints.

## [0.19.0] — 2026-06-04

### Added
- **Lean context in the live loop (opt-in `KORGEX_LEAN_CONTEXT=1`).** The agent now injects the past ledger events relevant to the current prompt as a compact, verified block (built by `recall`/`lean_context`), so a smaller/self-hosted model gets trustworthy context without carrying the whole history. Off by default; fail-safe (degrades to no block on any error, like memory recall); budget via `KORGEX_LEAN_CONTEXT_TOKENS` (default 800). This is the retrieve-don't-carry path wired end-to-end.
- **`korgex recall <query>` — lean, *verified* context from the ledger (retrieve, don't carry).** Pulls the past events relevant to a query and renders a compact, provenance-stamped block — each line says what was done, tagged with the `#seq` you can check (`korgex why` / `korgex verify`). Because the events are hash-chained, the retrieved memory is trustworthy, so a smaller (even self-hosted) model can run the loop on a short, lean prompt instead of carrying the whole history. New `src/lean_context.py` (`build_lean_context` with a token budget + `unresolved_refs` provenance check) builds on the existing `recall.search`. Documentation-first by design.
- **`korgex receipt` — mint a portable, signed, self-verifying proof of a run.** Exports a slice of the ledger as one shareable file: the events (so it verifies offline, no original journal needed), the chain tip, a human `--claim`, a summary (files / tools / cost), and an optional Ed25519 signature (`--sign`, authorship via a persistent `~/.korgex/identity.key` or `KORGEX_SIGNING_KEY`). Anyone checks it with `korgex receipt verify <file>` (exit-coded so CI can gate on a provable deliverable), or by opening the `--html` form in any browser — it re-verifies the hash chain locally, with zero trust in korgex (reuses the conformance-tested in-browser verifier and its live tamper test). The consumer edge of the verifiable-cognition moat.
- **Point korgex at a self-hosted / custom OpenAI-compatible endpoint.** Set `KORGEX_API_URL` (a self-hosted vLLM, LM Studio, or any OpenAI-compatible gateway) and korgex routes there — including for an arbitrary server-side model id like `Qwen2.5-Coder-32B`, which previously fell back to the Anthropic default. A keyless server needs no key (a placeholder is supplied). So `KORGEX_API_URL=http://your-box:8000/v1 korgex --model Qwen2.5-Coder-32B "…"` just works — the privacy / offline / cost-lever path, with nothing else to wire.

## [0.18.0] — 2026-06-03

### Added
- **`korgex review` — verifiable code review.** Reviews a diff (this branch vs a base, or `--staged` / `--working`) across correctness / security / performance / maintainability, then **adversarially verifies** each finding — a second pass that must confirm it's a real issue, not a plausible-but-wrong nit. Confirmed findings are printed and recorded as tamper-evident `review.finding` ledger events, so `korgex verify` / `trace` / `why <file>` can prove a review happened and what it found; the command exits nonzero on a confirmed high/critical finding, so CI can gate on it. The model only supplies candidate findings — the verdict is verified and the trail is auditable. This makes korgex the only agent whose *code review* is provable, not just advisory. (Hardened by a live wire-dogfood that caught a real parsing gap: different models name their JSON fields differently, so the parser is tolerant of synonyms, infers a missing dimension from the finding text, and fills a single-file diff's path.)

## [0.17.0] — 2026-06-03

Five features on top of 0.16.0 — session continuity and custom commands, plus three that extend the verifiable-cognition surface: the agent's own self-improvement, security scanning, and web search all become auditable / cross-vendor.

### Added
- **Verifiable self-improvement.** korgex learns reusable skills from your sessions and curates/ages them in the background — but those self-modifications were silent daemon-thread writes with swallowed errors and no record. Now every one is a first-class, causally-linked ledger event (`skill.learned` / `skill.updated` / `skill.curated` / `skill.swept`), each chained to the turn that caused it; a failed pass records a tamper-evident `skill.review_failed` verdict instead of vanishing. Read the trail with `/skills log` (REPL) or `korgex skills log` (CLI); `korgex why <skill>` traces a learned skill back to the prompt that taught it; `korgex verify`/`trace` show skill changes like any other action. The one place the agent modified *itself* is now as auditable as everything else.
- **`korgex scan` — verifiable security scanning.** Wraps the best scanner on your machine (trivy if present — vulnerabilities, leaked secrets, IaC misconfig, licenses; otherwise pip-audit / bandit) and turns its findings into **tamper-evident, causally-linked `security.scan` ledger events** tied to the exact code state — so `korgex verify`/`trace`/`why` can *prove* a scan happened and what it found, not just hand you an ephemeral report. `korgex scan [path]` prints findings and exits nonzero on a high/critical finding (a CI gate); a read-only `security_scan` agent tool lets the agent check code it just wrote; a `security-scan` skill teaches it when. korgex never reimplements a scanner — it wraps the one you have.
- **WebSearch: self-hosted SearXNG + opt-in stealth.** WebSearch scraped DuckDuckGo's HTML alone — single-engine and brittle. Set `SEARXNG_URL` and it queries your self-hosted [SearXNG](https://github.com/searxng/searxng) JSON API instead: private, keyless, multi-engine — falling back to DuckDuckGo automatically when SearXNG is absent or errors (each result tags its `engine`). Plus an opt-in stealth path: with `KORGEX_WEB_STEALTH=1` and the `camoufox` package installed, web fetches route through [Camoufox](https://github.com/daijro/camoufox) for pages a plain HTTP client gets bot-blocked on — default off, degrades to plain httpx when unavailable, and detected-not-bundled so it's never a hard dependency.
- **Session resume.** `korgex --resume` (and `/resume` in the REPL) reloads a prior session by replaying its **verifiable journal** back into context — so you pick up where you left off, grounded in the actual recorded prompts and actions rather than a lossy summary. `korgex sessions` lists what's resumable.
- **Custom slash commands + a bigger built-in skill library.** Define reusable prompts as markdown in `.korgex/commands/*.md` (frontmatter `description`/`argument-hint`, `$ARGUMENTS` / `$1..$9` substitution), layered built-in → project → user, invoked as `/<name>` — Claude-Code style. And the built-in skill library grew from 34 to 49 with a cherry-picked, MIT-attributed pack from [ECC](https://github.com/affaan-m/ECC) (accessibility, agentic-engineering, backend-patterns, code-tour, codebase-onboarding, and more).

## [0.16.0] — 2026-06-02

### Added
- **Authorized remote signer (opt-in, `RemoteSignTip`).** korgex can ask an HTTP signing service *you own and control* to sign a ledger tip and return a verified `{pubkey, tip, sig}` checkpoint — so the signing key can live **off the agent host** (a separate box, an HSM, a phone) instead of in the process. Fail-closed by design: bearer token required (`KORGEX_REMOTE_SIGNER_TOKEN`, read from env — never an argument, so it can't leak into the ledger), an explicit host allowlist (`KORGEX_REMOTE_SIGNER_ALLOWED_HOSTS`), strict hex validation, and the returned signature is **verified locally** before it's trusted. Optional hardening, all additive (nothing removed): `KORGEX_REMOTE_SIGNER_PUBKEY` **pins which key may sign** (without it the signature is only self-consistent — and the result says so); redirects are followed only within the allowlist and the bearer token is dropped on any host change; `KORGEX_REMOTE_SIGNER_REQUIRE_HTTPS=1` forbids plaintext http to non-loopback hosts (plaintext stays allowed by default, with a warning). This is for signer services you operate — **not** a mobile-app injection bridge.
- **Auditable network capture for the dev loop (opt-in, `NetCapture`).** Set `KORGEX_CODEACT`-style `KORGEX_NETCAPTURE_ENABLE=1` and the agent gets a `NetCapture` tool: it runs an app/script *you wrote* **under a local CA-signing capture proxy** (process-scoped — the proxy + CA trust are set on that subprocess's env only, never the system) and returns a structured trace of every HTTP(S) request/response — method, URL, status, headers, body, timing — so agents debug API calls (auth, headers, status codes) **without copy-pasting cURL**. Capture-only (traffic observed, never modified); secret header values + known-shape body secrets are masked before the trace is returned or recorded; every capture is a **verifiable ledger fact**, and a destructive command is refused by the same floor as Bash. Built on the `cryptography` lib korgex already ships — no heavy new dependency. Off by default (it's a TLS-intercepting capture proxy, opt-in like CodeAct/the browser). Known limit: cert-pinned third-party apps won't capture — but your own dev apps using normal HTTP clients will.
- **`korgex local` — hardware-aware local-model advisor.** korgex is hosted-provider-first; it had no notion of which *local* model fits your machine. `korgex local` shells out to [llmfit](https://github.com/AlexsJones/llmfit) (if it's on PATH) to turn detected hardware (CPU/RAM/GPU/VRAM) into a ranked, fit-scored recommendation — model · quant · est. tok/s · fit · run-mode — for the offline/privacy crowd. `korgex local --use <ollama-tag>` wires the pick as your default (local Ollama, OpenAI-compatible) and records the choice to the verifiable ledger, so there's a tamper-evident record of "on this hardware, korgex chose model X." llmfit is **optional and never bundled** — absent, you get a one-line install hint, not a crash. Also fixes model routing: an `ollama/<tag>` id now resolves to the Ollama provider (it previously fell into the OpenRouter `vendor/model` branch).

## [0.15.0] — 2026-06-02

The **autonomous-execution** batch — code-as-action, a destructive-command floor, and opt-in OS isolation, all on the verifiable ledger.

### Security
- **Destructive-command floor for Bash (on by default).** korgex's gates were path-based — Bash command *strings* were never inspected, so under the FREE-by-default policy `rm -rf /`, `dd of=/dev/sda`, a fork bomb, `chmod -R 777 /`, `curl | sh`, or `git push --force` ran unchecked (and CodeAct's `bash()` inherited the gap). A new `src/command_guard.py` semantically inspects each Bash command (whitelist-first, default-allow) and blocks the clearly-catastrophic categories — recording a **tamper-evident ledger verdict** that `korgex trace`/`why`/`verify` surface. False-positive control is the point: ordinary commands pass (`rm -rf ./build`, `/tmp/…`, `git push`), quoted DATA and COMMENTS never fire (`echo "rm -rf /"`), and a shell `-c` payload recurses one level while `python -c "…"` is treated as data. It's a floor against ACCIDENTS, not a sandbox (obfuscation evades regex). Off under `BYPASS` and `KORGEX_COMMAND_GUARD=off`; fails open.

### Added
- **CodeAct OS isolation (opt-in, Linux + bubblewrap).** `KORGEX_CODEACT_ISOLATION=1` wraps the CodeAct kernel subprocess in bubblewrap so model-authored code can only WRITE inside the workspace and has **no network** — forcing file-mutation and egress through the governed, ledger-recorded bridge (which runs in the unsandboxed parent), which makes CodeAct's "every action governed + traced" claim airtight rather than same-trust-as-Bash. **Fails closed**: if isolation is requested but Linux + `bwrap` aren't both present, the kernel refuses to start rather than run unconfined. Each code action records whether it ran sandboxed (`isolated`), so the trace can prove it. Off by default; bubblewrap was chosen over ctypes-Landlock because its confinement is declarative and auditable. (Linux-only; a future zero-external-dep Landlock/seccomp backend is possible.)
- **CodeAct — code as the action space (opt-in, experimental).** Set `KORGEX_CODEACT_ENABLE=1` and the model gets a `python` action whose code calls every other tool as a pre-defined function (`read_file`, `bash`, `edit`, `glob`, `grep`, `web_search`, `Retrieve`, `call_tool(name, **kwargs)`). Code runs in a **persistent, fuel-metered subprocess kernel** — variables/imports/defs survive across actions, so the model composes multi-step work (loops, intermediate values) in one action instead of many round-trips. Each bridged sub-call routes through the **same governed `route_tool_call` path** (edit-policy, hard-block floor, ledger) and records its own event **chained under the code-action seq** — a nested, replayable, tamper-evident causal DAG that `korgex trace`/`why`/`verify` prove. Fuel: per-exec wall-time (kernel compute only — parent tool time doesn't count), `RLIMIT_AS` memory (POSIX), output caps; a timeout/crash resets the kernel without hanging the loop. **Off by default** — it executes arbitrary model-authored code (same trust as Bash), so it ships available-but-off to bake in real use first.

### Fixed
- **`jsonschema` was an undeclared dependency.** `structured_output` hard-imports `jsonschema`, so schema-validated output crashed with `ModuleNotFoundError` on a clean `pip install korgex` — never caught because the maintainer's env already had it. Now declared in `dependencies`. (Surfaced running the suite on a clean Linux install for the first time.)

### Changed
- **CI now runs the test suite on Linux** (the deploy OS) on every push/PR. Previously the only workflows were the agent-bot and the PyPI publisher — the suite had only ever run on the maintainer's macOS, so platform-specific gaps (like the undeclared `jsonschema` above) could ship. Added `.github/workflows/tests.yml`.

## [0.14.3] — 2026-06-02

A patch over three bugs found dogfooding 0.14.2 on the wire — all green in the unit suite, broken when driving the real CLI.

### Fixed
- **Retrieve results were being re-compressed** (regression in 0.14.2's context compression). When the model called `Retrieve(ref)` to pull the full deferred bytes, that result re-entered `_compress_tool_result` and got sealed into *another* compact view — so the model never actually received the content and looped Retrieve→view→Retrieve until it stalled. Retrieve's whole job is to undo compression, so its output is now exempt and reaches the model verbatim. (Found dogfooding on the wire; the unit tests exercised `tool_retrieve_blob` directly, never the agent-loop path.)
- **`korgex verify` crashed on real session journals.** The live (default/bridge) ledger writes a pretty-printed JSON *array*, but `verify_journal_file` parsed strictly line-by-line and raised `JSONDecodeError` — so the flagship "prove the ledger intact" command was broken for the journals actual runs produce, while `korgex trace` (array-aware) worked. `verify` now reads both shapes (array and JSONL) raw, and the event count is correct (was counting lines).
- **Compaction token counts were redacted from the ledger.** The cache-aware compaction event records `tokens_before`/`tokens_after`; these contain "token" and were being scrubbed to `[REDACTED]` by the secret-key sanitizer, destroying `trace`/cost data. They're now whitelisted as counts (alongside `prompt_tokens`/`completion_tokens`).

## [0.14.2] — 2026-06-02

The **context-efficiency + portable-skills** batch — korgex keeps its context window lean without losing data, and its skill loader gets more portable.

### Added
- **Verifiable context compression.** Large tool results no longer flood the model's context. The full result is sealed once as a hash-chained content-ref (sha256) and the model instead sees a compact, structure-aware view (JSON / Python / text aware) plus a `Retrieve(ref)` tool that returns the exact original bytes, sha256-verified — nothing is lost, only deferred. Credentials are redacted *before* sealing, so a secret in tool output never reaches the (shareable) blob store or the model view. Each compression is a `context.compress` ledger fact chained to the call that produced it, so `korgex trace`/`verify` prove what was folded away and that the original is recoverable.
- **Cache-aware compaction.** Compaction and provider prompt-caching used to work against each other: rewriting the cached prefix busts the cache for zero benefit. Compaction now (a) never rewrites the provider-cached leading turns (the "frozen prefix") and (b) only forces a rebuild when the tokens it reclaims actually beat the one-time cost of busting the cache — absolute token economics with a per-provider cache-read discount, not a dimensionally-wrong fraction test that effectively disabled compaction on warm Anthropic sessions. The decision (fire or skip, and why) is recorded on the compaction ledger event (`cache_read_before`, `frozen_prefix_turns`, `savings_fraction`, `decision_reason`).
- **Flat-file skill loading.** The skill loader previously scanned only the `<name>/SKILL.md` directory layout; it now also loads flat `<name>.SKILL.md` files in a skills root, so a single-file or cross-runtime skill library drops into `~/.korgex/skills` and just works — the name comes from frontmatter, both layouts coexist.
- **Two built-in skills** teaching korgex's own newest differentiators (built-in tier, 34 total): **browser-automation** — the verifiable `browser_*` loop (navigate → snapshot → act *by index* → re-snapshot → extract), the untrusted-content rule, opt-in stealth, `browser_evaluate` gating, and when to prefer `browser_fetch`/`audit`/`crawl`; and **verifiable-orchestration** — when to use parallel `Agent` calls vs the `Orchestrate` DAG, the immutable spec-seed for pinning intent, and the one-level-nesting rule.

## [0.14.1] — 2026-06-02

### Added
- **Immutable spec-seed for Orchestrate runs.** Pass `seed` (goal + constraints + acceptance criteria) to the `Orchestrate` tool and it's recorded as a hash-chained `spec.seed` event that the whole run anchors under. `korgex why`/`trace` then walk any result back to the spec it was meant to satisfy, and `korgex verify` proves the spec wasn't altered after the fact. Absent → behavior unchanged.

## [0.14.0] — 2026-06-02

The **multi-agent + browser** batch — korgex grows its own verifiable orchestration and a verifiable browser.

### Added
- **Parallel subagents** — multiple `Agent` calls in one turn now fan out concurrently (`KORGEX_PARALLEL_AGENTS`, default 4) on a thread-safe ledger; results stay in call order and a crashed sibling is isolated. One-level nesting is hard-enforced at dispatch.
- **`Orchestrate` workflow primitive** — run a user-defined DAG of subagents (nodes + deps) via the in-process exec graph. The whole run — including the failure topology (node-failed / node-skipped) — is ONE connected, replayable, tamper-evident causal DAG that `korgex trace`/`verify` prove. No mainstream agent framework emits that artifact.
- **Verifiable browser suite** (`pip install korgex[browser]`) — a CDP snapshot→act loop the model drives BY INDEX: `browser_navigate`/`snapshot`/`click`/`type`/`extract`/`screenshot`/`evaluate`/`wait`/`scroll`, plus tiered `browser_fetch` (fast HTTP → browser render → opt-in stealth) with AI-hardened extraction, scoped `browser_crawl` (normalized-URL dedup, same-host rail, rate-limit, session-scoring), and a deterministic sealable `browser_audit`. Every perceive/act records a verifiable trace (pre/post snapshot hash, index, driver) to the ledger, so a whole browser session is replayable and auditable. The stealth driver is opt-in and **recorded** on every trace, never hidden. Optional deps are imported lazily — the core install and the test suite never require a browser.

### Changed
- `browser_evaluate` (arbitrary JavaScript on an untrusted page) is gated default-off behind `KORGEX_BROWSER_EVAL` — explicit opt-in.

## [0.13.1] — 2026-06-02

Routing fix — the BYO-OAuth providers from 0.13.0 weren't actually reaching their own endpoints when a gateway key was configured.

### Fixed
- **Grok models were silently routed to OpenRouter instead of xAI.** With an OpenRouter key configured, `_get_client` skipped the BYO-OAuth path (it only ran "when no api-key"), so every grok model went to OpenRouter — which serves `grok-4.3` but not `grok-4.20-0309-*`, so `grok-reasoning` / `grok-mini` returned `400 not a valid model ID`. A grok model now uses its dedicated xAI endpoint whenever a local token is available, falling back to the configured key only when there's no token. All three grok variants verified on the wire.

### Changed
- **Gemini falls back to the configured key (e.g. OpenRouter).** The local Google (Antigravity/ADC) OAuth token is `401`'d by the `generativelanguage` endpoint (scope/project mismatch), so Gemini is no longer routed through that OAuth path.
- CI: bumped `actions/checkout@v4→v5` and `actions/setup-python@v5→v6` (Node 20 deprecation).

## [0.13.0] — 2026-06-02

The **multi-provider** batch — bring your own login. korgex now reaches Grok, Gemini, the Nous gateway, and Venice using the same credential their own CLI/app already stores, plus dollar-cost accounting straight from the verifiable ledger.

### Added
- **Bring-your-own-OAuth providers, wired into the live agent loop.** When no api-key is configured, korgex mints a bearer token from the credential the provider's own CLI/app already holds and routes through its OpenAI-compatible endpoint — no separate key to manage:
  - **Grok** — `--model grok4` (→ `grok-4.3`), via the local grok login.
  - **Gemini** — `--model gemini-flash` / `gemini-pro`, via the local Google login.
  - **Nous gateway** — `--model nous/<vendor/model>` (e.g. `nous/anthropic/claude-opus-4.8`): one subscription, many models (Claude Opus 4.8, Gemini, Grok, Qwen, …).
  - **Venice** — `--model venice/<model>`, via `VENICE_API_KEY`.
  - A configured api-key always takes precedence; short aliases resolve to concrete model ids.
- **Dollar-cost from the ledger** — `korgex cost [journal]`, a one-line cost footer on `korgex trace`, and `/cost` in the REPL. Token counts come from the verifiable ledger; the dollar figure is an honest estimate against public list prices (and says so).

### Fixed
- **Secret-scrubber over-redaction** destroyed token counts — `prompt_tokens` / `completion_tokens` matched the "token" rule and were written as `[REDACTED]`, breaking cost + audit data. Added an allowlist of count/config keys.
- **OAuth token-expiry coercion** — a stored `expires_at` is now coerced (float / numeric string / ISO-8601; ms for the keychain) before comparison, fixing a `float > str` crash that killed token loads.
- **Nous client key-refresh** referenced `datetime`/`timezone` without importing them in scope (NameError when saving a refreshed key).

### Changed
- **Opsec pre-commit guard tightened** to flag only genuine RE artifacts — legitimate provider names/domains (a paid gateway's endpoint + OAuth client) no longer false-positive, while the real leak signals stay blocked. Added a hook-invocation test.

## [0.12.2] — 2026-06-02

The **verifiable cognition** batch — korgex's tamper-evident audit ledger, made legible. The thing the closed agents can't offer: cognition you can both *read* and *prove*.

### Added
- **Cognition trace** (`src/ledger_trace.py`): `korgex trace [journal]` and `/trace [all]` reconstruct the causal DAG every event carries (user_prompt → llm_inference rounds → the tool_calls each round caused) into a readable tree of what the agent did and *what caused it*.
- **`/why <file>`** + **`korgex why <file> [journal]`**: trace why a file changed — back through the causal chain to the prompt that caused it, one scannable line per touch.
- **`/explain`**: open a self-verifying HTML cognition audit — what the agent did, token cost, the hash chain, and a **live in-browser tamper test**. `/explain on` (or `KORGEX_EXPLAIN=1`) auto-opens it after every run. Re-verifies locally; no trust in the tool that made it.
- **Auto-publish to PyPI on release** via Trusted Publishing (`.github/workflows/publish.yml`) — OIDC, no stored token.
- **Opsec pre-commit guard** (`scripts/githooks/pre-commit`) that keeps sensitive vendor-internal material out of the public repo.

### Changed
- Docs: full README + `cli-reference` refresh — current install paths, the `--version`/`-V` flag, the `korgex skills` / `korgex setup` / `korgex trace` / `korgex why` subcommands, and the new REPL commands.

## [0.12.1] — 2026-06-01

Reliability pass — real bugs found by running korgex on actual coding tasks (dogfooding) and backfilling tests on the untested core. Every fix is locked with a test.

### Fixed
- **`korgex "task"` used the wrong model.** The naked-prompt path ignored your configured `default_model` and fell back to claude-sonnet — sending an OpenRouter key to Anthropic as `x-api-key` → 401. Now honors precedence: explicit `--model` → `--mode` → `config.default_model` → `KORGEX_MODEL` → builtin.
- **`korgex "task"` printed nothing** in script / non-TTY mode — the final answer only emitted under `--quiet`. Now prints whenever it wasn't streamed live.
- **`list_files` returned nothing on macOS** — it used the GNU-only `ls --group-directories-first`, which BSD `ls` (macOS) rejects, so every directory looked empty. Now the portable `ls -a -1F`.
- **The Edit handler returned a hardcoded string**, discarding the real filepath/changes — restored the real result and added the regression test it never had.
- **Control-byte corruption in edits** — a mangled em-dash could reach disk as `\x1a\x14`. Write/Edit now strip C0/DEL control characters at the write boundary (all printable text and Unicode pass through).
- **Bare `pytest` failed** with `ModuleNotFoundError: src` (no `pythonpath` config) — added `pythonpath = ["."]`, so self-verification and CI work under any invocation.
- **`humanize_error`** now also maps 503 / service-unavailable / overloaded to clear guidance.

### Added
- `korgex --version` / `-V` flag.
- `/version` REPL command.
- `korgex skills` subcommand — lists every available skill (project-aware).

### Tests
- Backfilled coverage on the core tool handlers (Read / Write / list_files / delete / Bash / Edit) that previously had none — closing the gap that let regressions slip through.

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
- Support for the RAG context-injection format + live SSE streaming event shapes.

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
