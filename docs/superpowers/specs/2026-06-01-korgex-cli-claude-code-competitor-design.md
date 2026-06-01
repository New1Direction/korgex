# korgex CLI — the cross-vendor Claude Code competitor

**Date:** 2026-06-01
**Status:** approved (founder: "just set it up, this is Korg's Claude Code competitor")

## The pitch

Every other agent CLI funnels you into one vendor's models. **korgex runs them all** — provider-agnostic, beholden to no ecosystem. Install it anywhere, `korgex setup` to connect any provider (one OpenRouter key = hundreds of models; or direct Anthropic/OpenAI; or local Ollama with no key), then `korgex` drops you into a streaming, multi-turn agent session that feels like Claude Code — and you can swap models mid-conversation.

This repositions korgex from "one-shot task runner + VS Code backend" to a **terminal-native, conversational, cross-vendor coding agent**. It builds on what already exists: the streaming agent loop (`agent.py`), the multi-provider router (`model_router.py` already has `ModelConfig.provider ∈ {anthropic, openai, openrouter, local}`), the ledger, MCP, modes, effort. What's missing is the *shell*: global install, a config layer, a setup wizard, and a REPL.

## Scope — decomposed

This is 5 subsystems. We build in slices; this spec covers **Slice 1 (the spine)** in full and sketches Slice 2.

1. **Global install** — `korgex` runs in any terminal (Slice 1)
2. **Config + setup** — `~/.korgex/config.toml`, `korgex setup` wizard, any provider (Slice 1)
3. **REPL** — stay-in-it streaming multi-turn session (Slice 1)
4. **Session powers** — `/commands`, `/model` mid-chat, history, resume (Slice 1 minimal: `/model`, `/help`, `/exit`, `/clear`; Slice 2 deeper)
5. **"More than everything out"** — expose already-built powers (subagents, MCP, plan-mode, memory, effort) as REPL commands (Slice 2)

## Slice 1 — the spine (this build)

### A. Config layer — `~/.korgex/config.toml`

A new module `src/config.py`. Pure functions over a JSON file at `~/.korgex/config.json` (override `$KORGEX_CONFIG`). **JSON, not TOML** — the target runs Python 3.9 which has no stdlib `tomllib` (3.11+), and adding a TOML dep risks the clean-install crashes korgex has been bitten by before; JSON is stdlib, zero-dep. Schema is a **list of providers** so "all providers" is just list entries — adding one later never rewrites anything:

```json
{
  "default_model": "claude-opus-4-8",
  "providers": [
    { "type": "openrouter", "api_key": "sk-or-..." },
    { "type": "anthropic",  "api_key": "sk-ant-..." },
    { "type": "ollama",     "base_url": "http://localhost:11434" }
  ]
}
```

- `load_config() -> Config` — reads the file; missing file → empty config (never raises).
- `save_config(cfg)` — writes TOML, `chmod 600` (keys are secrets on disk).
- `Config` exposes: `default_model`, `providers` (list of `{type, api_key?, base_url?}`), and helpers `provider_for(type)`, `api_key_for(type)`, `is_configured() -> bool`.
- `resolve_model_and_key(model, config, env)` — precedence: explicit arg → config default → env (`KORGEX_MODEL`) → built-in default. Maps the chosen model's provider to its saved key (falling back to env keys so existing `ANTHROPIC_API_KEY` users keep working). This is the seam the agent reads instead of bare `os.environ`.
- TDD: round-trip save/load; missing file → empty; chmod is 600; resolution precedence; env fallback.

**Honest limit:** keys stored as plaintext TOML (chmod 600), matching how dev tools (aws/gh/npm) do it locally. Not encrypted-at-rest in Slice 1; documented, not hidden.

### B. `korgex setup` wizard — `cmd_setup` in `cli.py`

Added to the `SUBCOMMANDS` table. Interactive, friendly, provider-agnostic:
1. Pick a provider (openrouter / anthropic / openai / ollama / "add another").
2. Enter its API key (hidden via `getpass`; ollama asks for a base_url instead, no key).
3. Pick a default model (sensible suggestions per provider; free-text allowed — never a hardcoded allowlist, since we're not locked to any catalog).
4. Offer to add another provider (loop), then save to `~/.korgex/config.toml` (chmod 600) and confirm where it went.
- Re-running `setup` edits the existing config (shows what's already connected).
- **Prohibited-action note:** the wizard PROMPTS the user to type their own key (the user enters it); korgex never fetches/creates keys. This is fine — it's the user entering their own secret into their own local config, the same as `gh auth login`.
- TDD: the pure pieces — provider validation, the config object the wizard assembles from given answers (inject answers, assert the saved Config), default-model suggestion per provider. The `getpass`/`input` I/O stays a thin shell over the tested core.

### C. The REPL — `src/repl.py`, launched by bare `korgex`

When `korgex` is run with **no args and no task** (and stdout is a TTY), launch the REPL instead of printing help. (Today bare `korgex` prints help; `korgex "task"` one-shots. New: bare `korgex` → REPL; `korgex "task"` → one-shot still works for scripts/CI.)

- A `Repl` class holds: the `KorgexAgent`, the conversation state, the active model.
- Loop: print a prompt (`› `), read a line, and:
  - line starts with `/` → dispatch a **slash command** (see below)
  - otherwise → it's a turn: stream the agent's response (reuse the existing streaming path — `interactive=True`), keep the conversation in context across turns (multi-turn is the whole point; today `run_task` is single-shot, so the REPL maintains the message history and the agent continues it).
- **Multi-turn:** the REPL holds the running conversation so turn 2 sees turn 1. (Implementation: keep the agent instance + accumulated messages between turns rather than a fresh `run_task` each time — extend the agent with a `continue_task`/session-message-list, or have the REPL own the message list and feed it in. Decide in the plan; the seam is the agent's message history.)
- First-run: if `not config.is_configured()` and no env key → the REPL greets with "let's connect a model" and runs the setup flow inline (or points to `korgex setup`).

**Slash commands (Slice 1 minimal):**
- `/model [name]` — no arg: list configured providers' suggested models + show current; with arg: **switch the live model mid-session** (option C). Re-resolves provider+key from config.
- `/help` — list commands.
- `/clear` — reset the conversation context.
- `/exit` (and Ctrl-D / Ctrl-C) — leave cleanly, restore terminal.
- (Slice 2: `/plan`, `/mode`, `/mcp`, `/resume`, `/effort`, `/cost`, `/agents` …)

- TDD: the **command parser/dispatcher** is pure (`parse_repl_input("/model gpt-4o") -> Command::Model("gpt-4o")`, `"hello" -> Command::Turn("hello")`, `/help`, `/exit`, unknown → error). The streaming/IO loop is a thin shell over the tested dispatcher + the already-working agent stream.

### D. Global install

- `pyproject.toml` already declares `korgex = "src.cli:main"` under `[project.scripts]` — so `pip install -e .` (or a published wheel) already puts `korgex` on PATH. Slice 1: confirm/repair this works, document `pipx install korgex` as the clean global path (pipx = isolated, the right tool for a CLI). No new code likely needed — verify, don't rebuild.

### Dispatch change (cli.py `main`)
- Bare `korgex` (argv empty, TTY) → launch REPL (was: print help).
- `korgex "task ..."` → one-shot (unchanged).
- `korgex setup` → wizard (new subcommand).
- `korgex --help`, piped/non-TTY bare → help (unchanged, so scripts/CI don't hang on a REPL).

## Architecture / data flow

```
korgex (bare, TTY) ──► repl.Repl
                          │  owns conversation + active model
                          ├─ parse_repl_input() ─► Command (pure, tested)
                          ├─ /model ─► config.resolve_model_and_key() ─► swap agent model
                          └─ turn ──► KorgexAgent stream (existing) ─► render live
korgex setup ──► cmd_setup ─► (prompts) ─► config.save_config()  [~/.korgex/config.toml, 600]
korgex "task" ──► run_agent_shim (existing one-shot)  [unchanged]
KorgexAgent ──► reads config.resolve_model_and_key() instead of bare os.environ for key
```

New units, each independently testable:
- `src/config.py` — config load/save/resolve (pure; file I/O isolated).
- `src/repl.py` — `Repl` + `parse_repl_input` (parser pure; loop is the shell).
- `cmd_setup` in `cli.py` — wizard (assembled-config core tested; prompts are shell).
- minimal change in `agent.py`/`model_router` — read resolved key from config (env fallback preserved).

## Error handling
- No config + no env key → friendly "run korgex setup" (never a stack trace).
- Bad/expired key → surface the provider's error cleanly in the REPL, stay in the session.
- Unknown slash command → "unknown command, try /help", stay in session.
- Non-TTY / piped → never launch REPL (would hang); fall back to help or one-shot.
- Config file unreadable/corrupt → treat as empty + warn, don't crash.

## Testing strategy
- TDD every pure unit: config round-trip + resolution precedence + chmod; `parse_repl_input` cases; wizard's assembled-config; default-model suggestion.
- The IO shells (getpass prompts, the readline loop, the live stream) are thin over tested cores — verified by running it, not unit-mocked to death.
- Full `pytest` stays green; no regression to the one-shot path (existing tests cover it).
- Manual: founder runs `korgex setup` then `korgex`, confirms the multi-turn streaming + `/model` swap feels like Claude Code.

## Out of scope (Slice 2+)
Deeper `/commands` (`/plan`, `/mode`, `/mcp`, `/resume`, `/effort`, `/cost`, `/agents`), session persistence/resume, the "more features than everything out" surface (exposing subagents/plan-mode/memory in-REPL), encrypted-at-rest keys, the VS Code path's relationship to the CLI. Each its own spec.
