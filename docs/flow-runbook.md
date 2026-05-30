# Flow runbook — verifiable cognition, end to end

The two flagship flows the korg-ledger story rests on, as exact, reproducible
commands. Every command below is exercised by `tests/test_flow_e2e.py`, so this
runbook and the test move together — if a command here drifts, that test fails.

The guarantee throughout: a session becomes a **korg-ledger@v1** hash-chained
journal. Edit, delete, reorder, or splice any event and the chain breaks, and the
break is localized to a `seq_id`. The same algorithm runs in Python, Rust, and JS
against frozen conformance vectors — the HTML report re-verifies **in the
recipient's own browser**, so they need not trust the tool that made it.

```
korgex audit ──► korg-ledger@v1 journal ──► korgex verify
       │                                          ▲
       └──► self-verifying HTML report ───────────┘ (re-verifies in any browser)
```

Prereqs: Python 3.10+ on PATH, `korgex` installed (`pip install -e .` from the
repo). `node` is optional — only needed if you want to run the report's embedded
verifier headless (the browser does it for free).

---

## Flow 1 — Claude Code session → ledger → verify → shareable report

You already have the logs: Claude Code writes every session to
`~/.claude/projects/**/*.jsonl`. Point korg at them and get an instant,
tamper-evident audit. No setup, no buy-in.

### 1. Audit the newest Claude Code session (zero-config)

```bash
korgex audit --html
```

`audit` discovers the newest session under `~/.claude/projects`, imports it into a
korg-ledger@v1 chained journal under `~/.korgex/audits/`, verifies the chain, and
(`--html`) writes a self-verifying report next to the journal.

### 2. Audit a specific session, to chosen paths

```bash
korgex audit \
  --session /path/to/session.jsonl \
  --out     audit.korg.jsonl \
  --html    audit.html
```

Expected output:

```
  audited session.jsonl → 6 ledger events
  activity: llm_inference×2, user_prompt×1, Read×1, Edit×1, Bash×1
  journal:  audit.korg.jsonl
  report:   audit.html  ← open in any browser; it re-verifies itself
  chain:    ✓ INTACT — tamper-evident, cryptographically verifiable
  re-check any time:  korgex verify audit.korg.jsonl
```

What the adapter does: each user prose turn → one `user_prompt`; each assistant
turn → one `llm_inference` plus one event per `tool_use` (`Read`/`Edit`/`Bash`/…),
with `triggered_by` reconstructed from the transcript's parent pointers.
Tool-result-only user turns (no prose) are dropped. Secrets are redacted before
they reach the (shareable) ledger.

### 3. Re-verify the chain on disk, any time

```bash
korgex verify audit.korg.jsonl
#   ✓ ledger intact — 6 events, hash-chain verified
```

### 4. Open the report — it re-verifies itself

```bash
open audit.html          # macOS  (xdg-open on Linux)
```

The page recomputes every `entry_hash` locally with the embedded korg-ledger@v1
verifier, shows the verdict (CHAIN INTACT), and has a live **Tamper test** button:
it edits one recorded event in memory, re-runs the same verifier, and the verdict
flips to TAMPERED pinpointing the broken event. No network calls, single file —
safe to email or attach to a PR.

### 5. Prove tamper-evidence from the CLI (optional)

```bash
# doctor one event's args, leaving its stale entry_hash in place
python3 - <<'PY'
import json
e=[json.loads(l) for l in open("audit.korg.jsonl") if l.strip()]
e[1]["args"]["x"]="doctored"
open("tampered.korg.jsonl","w").write("\n".join(json.dumps(x) for x in e)+"\n")
PY

korgex verify tampered.korg.jsonl
#   ✗ ledger TAMPERED — 1 problem(s):
#       - seq 2: entry_hash mismatch (content was tampered)
```

---

## Flow 2 — Witness tap on a tool-dispatch loop → verify → report

`integrations/witness/witness.py` is a self-contained korg-ledger@v1 tap for **any**
`handle_tool(name, arguments) -> result` choke point: an MCP server, an agent loop,
a CLI router. It is stdlib-only — the dispatcher it wraps needs no dependency on
korgex.

### 2a. Tap a live dispatcher (two lines)

```python
from witness import tap
handle_tool = tap(handle_tool)        # opt-in via $KORG_TAP_JOURNAL
```

The tap is a no-op (zero overhead) unless a journal path is supplied. Enable it:

```bash
export KORG_TAP_JOURNAL=tap.korg.jsonl
python your_dispatcher.py             # every handle_tool call is now recorded
korgex verify tap.korg.jsonl
#   ✓ ledger intact — N events, hash-chain verified
```

Two production guarantees: the wrapped function returns the underlying result
**unchanged** (pass-through), and a ledger write failure can **never** break a tool
call (fail-safe — logging is best-effort). It also **resumes** an existing
journal's chain across process restarts.

### 2b. Import an existing witness journal (no live tap)

If a tool-dispatch log already exists (with `parent_id` causal pointers), re-chain
it after the fact:

```bash
korgex import witness witness.jsonl --out witness.korg.jsonl
#   imported 2 events from 'witness' → witness.korg.jsonl
#   chain: ✓ verified intact    ·    inspect: korgex verify witness.korg.jsonl
```

`parent_id` lineage and artifact provenance (`artifact_hash`, hostnames,
timestamps) are carried onto the tamper-evident chain.

### 2c. Render the same self-verifying report

The witness chain renders into the identical HTML report as Flow 1 — its embedded
JS verifier reproduces the Python chain tip byte-for-byte:

```bash
korgex audit --session tap.korg.jsonl --html tap.html  # works directly on a tap journal
```

---

## Tamper-PROOF mode (HMAC) — both flows

Without a key the chain is tamper-**evident** against a trusted tip. Set an HMAC
key and it becomes tamper-**proof**: a tail rewritten without the key fails even
though it is internally self-consistent.

```bash
export KORG_LEDGER_HMAC_KEY="your-team-secret"

# write WITH the key (audit / tap both honor it)…
KORG_TAP_JOURNAL=tap_hmac.korg.jsonl python your_dispatcher.py

# …and verify WITH the same key
korgex verify tap_hmac.korg.jsonl
#   ✓ ledger intact — 2 events, hash-chain verified (HMAC-keyed)
```

The key must match between write and verify. Verifying a key-less journal *with* a
key (or vice-versa) reports every event as tampered — by design.

---

## Reproducing the proofs

```bash
python3 -m pytest tests/test_flow_e2e.py -v       # both flows, end to end
python3 -m pytest tests/test_audit_report.py -v   # JS verifier == Python tip on frozen vectors
```

`tests/test_flow_e2e.py` is the executable form of this runbook: it runs `korgex
audit --html`, `korgex verify`, the witness tap, and `korgex import witness`
through the real CLI, then runs the report's **embedded** JS verifier (in node)
and asserts its tip equals the Python tip — and that both localize a tampered
event to the same `seq_id`. That cross-language agreement is the whole claim.
```
