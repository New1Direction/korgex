# Egress / exfil guard — design

**Date:** 2026-06-05
**Status:** approved (brainstorming) → ready for implementation plan
**Branch:** `feat/egress-guard`

## Problem

korgex can transmit data off-box through several tools (web, browser, bus, MCP, remote-sign, network Bash). Nothing today inspects what leaves the machine, so a prompt-injected or confused agent could exfiltrate a secret (a provider key, a private key, a token) to an external destination, and there would be no record of it. korgex's differentiator is a verifiable audit ledger; an **egress guard that records every outbound-data risk as a tamper-evident verdict** reinforces that moat while adding a real safety floor.

## Goals

- Detect **secret shapes** and **possible encoded exfil** in the payload of outbound tool calls.
- Record every detection as a tamper-evident **ledger verdict** (the audit value), with the secret value itself redacted from the record.
- Default to **flag** (warn + ledger, never alter/block) so it is additive — zero false-positive breakage out of the box (korgex's standing rule: harden additively, never remove ability).
- Offer opt-in **redact** and **block** modes for users who want enforcement.

## Non-goals (v1)

- Deep content/DLP classification (regex/shape-based only).
- TLS interception or network-layer capture (that is `netcapture`'s separate, opt-in job).
- Flagging "odd" destinations heuristically — destination is **recorded** and supports an **opt-in** allow/deny host list, but unknown hosts are not flagged by default (too false-positive-prone).

## Decisions (locked in brainstorming)

| Decision | Choice |
|---|---|
| Default posture | **flag** (warn + ledger; never alters or blocks) |
| Default state | **on by default** in flag mode (flag never breaks anything); `KORGEX_EGRESS=off` disables |
| Stricter modes | **opt-in**: `KORGEX_EGRESS=redact` (mask secret in outbound payload) or `=block` (refuse) |
| v1 detection | secret shapes (reused) **+** large base64/high-entropy blob **+** destination recorded + opt-in allow/deny host list |
| Reuse | secret-shape detection reuses `src/sanitize.py` patterns (no second pattern set to drift) |

## Architecture

One **pure** module + one **dispatch hook**.

```
route_tool_call(tool, params)                 # src/tool_abstraction.py (existing seam)
        │
        ├─ egress_guard.is_outbound(tool, params)?  ── no ──▶ run tool normally
        │                                              yes
        ▼
   egress_guard.inspect(tool, params) ──▶ EgressVerdict{findings, severity, destination}
        │
        ├─ record verdict to ledger as egress.<mode> (secret value redacted first!)
        │
        ▼
   egress_guard.apply(verdict, params, mode)
        ├─ flag   → params unchanged, warn line               → run tool
        ├─ redact → params with secrets masked                → run tool (masked)
        └─ block  → no run; return a refusal result (egress.block verdict)
```

### Why this seam
`route_tool_call` is the single central dispatch every tool flows through. One hook keeps the logic in one place, reusable and testable, instead of scattering checks across `web_tools`, `bus`, `browser`, the MCP router, etc. Rejected alternatives: (B) a `hooks.py` PreToolUse hook — its shell-command contract is awkward for in-process secret scanning; (C) per-tool inline checks — scattered, DRY-violating, drift-prone.

## Module: `src/egress_guard.py` (pure, isolated)

Constants / config:
- `OUTBOUND_TOOLS = {"WebFetch", "WebSearch", "BusSend", "RemoteSignTip", "browser_navigate", ...}` and MCP-sourced tool names (via the existing `_MCP_TOOLS` set).
- `_NETWORK_CMDS = {"curl", "wget", "nc", "ncat", "scp", "ssh", "rsync", "ftp", "telnet"}` for Bash classification.
- `_BASE64_RE` + `_MIN_BLOB` (threshold, e.g. 512 chars) for the encoded-blob signal.
- Mode/config read from `KORGEX_EGRESS` env + `config` (`egress_mode`, `egress_allow`, `egress_deny`).

Functions:
- `is_outbound(tool_name: str, params: dict) -> bool` — membership in `OUTBOUND_TOOLS`/MCP, **or** Bash whose command contains a network command. Local tools → False.
- `outbound_text(tool_name: str, params: dict) -> str` — the payload that would leave the box per tool (WebFetch/browser URL, WebSearch query, BusSend body, Bash command, MCP args json). Single place that knows each tool's "wire" field.
- `extract_destination(tool_name: str, params: dict) -> str | None` — host/URL where extractable (URL host, Bash remote host, MCP server name).
- `scan_payload(text: str) -> list[Finding]` — secret-shape findings (reuse `sanitize` regexes) + base64-blob findings. `Finding = {kind, label, severity}` — **never the matched value**.
- `inspect(tool_name, params, *, allow=None, deny=None) -> EgressVerdict` — composes the above; `EgressVerdict = {findings, severity, destination, denied_by_list: bool}`.
- `apply(verdict, params, mode) -> tuple[dict, str]` — returns `(new_params, action)` where action ∈ `{"allow","redacted","blocked"}`; `redact` masks secrets in `outbound_text`'s field using `sanitize.redact`-style masking; `block` returns params unchanged with action `"blocked"`.
- `mode_from_env(env) -> str` — `off|flag|redact|block`, default `flag`; garbage → `flag`.

### Ledger verdict (recorded in `tool_abstraction`)
Event `egress.flag` / `egress.redact` / `egress.block`, payload:
```json
{
  "tool": "WebFetch",
  "destination": "evil.example.com",
  "findings": [{"kind": "secret", "label": "openai_key", "severity": "high"}],
  "severity": "high",
  "mode": "flag",
  "action": "allow",
  "policy_hash": "<sha256 of the active mode+lists>"
}
```
**Leak-proofing invariant (critical):** the payload is passed through `sanitize.redact()` before recording, and findings carry only `kind/label/severity` — never the matched secret. The ledger can be shared (`korgex receipt/share`), so it must never itself become the exfil channel.

## Error handling

- Detection is best-effort and **fail-open in flag mode**: any exception inside `inspect`/`scan` is swallowed (returns no findings) so the guard can never crash a tool call. In `block` mode a scan error is treated as "no finding" (do not block on a guard bug) — never fail-closed on our own error.
- `outbound_text` tolerates missing/oddly-shaped params (returns `""`).

## Modes & config precedence

`KORGEX_EGRESS` env overrides `config.egress_mode`; default `flag`. `egress_allow`/`egress_deny` host lists (config only) gate `block`/flag-deny independent of secret findings (a denied destination flags/blocks even with no secret). `off` short-circuits `is_outbound` so there is zero overhead.

## Testing (TDD, pure-first)

Unit (no network, no real ledger):
- `is_outbound`: web/bus/browser/MCP True; Read/Write/Grep/local-Bash False; `Bash` with `curl ...` True, `Bash` with `ls` False.
- `outbound_text`: extracts the right field per tool; tolerates missing keys.
- `scan_payload`: each secret shape (sk-, gh*_, AKIA, AIza, xox*, JWT, PEM) detected; clean text → none; base64 blob ≥ threshold detected, short base64 ignored.
- `inspect`: severity rollup; destination attached; deny-list hit sets `denied_by_list`.
- `apply`: flag → params unchanged/action allow; redact → secret masked in the outbound field, rest intact; block → action blocked.
- `mode_from_env`: off/flag/redact/block + garbage→flag.
- **Leak-proofing:** the recorded verdict payload (post-redact) contains no raw secret, for every shape.

Wired (injected ledger):
- `route_tool_call` in each mode: flag runs + records `egress.flag`; redact runs with masked params + records `egress.redact`; block returns refusal + records `egress.block`; `off` records nothing and runs normally.

Then **wire-dogfood**: drive the real CLI doing a `WebFetch`/`Bash curl` carrying a fake `sk-` token in each mode; confirm the warn line, the ledger verdict (`korgex trace`/`verify`), masked payload in redact, refusal in block.

## Files

- `src/egress_guard.py` (new, pure)
- `src/tool_abstraction.py` (wire `inspect`/`apply` + ledger record into `route_tool_call`)
- `src/config.py` (read `egress_mode`/`egress_allow`/`egress_deny` — optional, env works without it)
- `tests/test_egress_guard.py` (new)
- `README.md` + `CHANGELOG.md` (document `KORGEX_EGRESS`)

## Rollout

Ships in the next release. Flag mode on by default (additive); `KORGEX_EGRESS=off` to silence, `=redact`/`=block` to enforce. Follow-ups (deferred): entropy-tuned blob detection, per-destination policy beyond allow/deny, redaction of secrets in *inbound* tool results before they reach the model.
