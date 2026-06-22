# InMemoryLedgerClient + `_build_body` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a chain-faithful `InMemoryLedgerClient` (record → `verify_chain` with no I/O) and extract a shared `_build_body` so event-body construction lives in one place — closing a latent redaction gap on the HTTP `record_tool_call` path along the way.

**Architecture:** `_build_body(tool_name, args, result, success, duration_ms, triggered_by, source_agent) -> (body, payload_refs)` centralizes redact → content-ref → the 8-field event body. The HTTP transport, `LocalJournalClient`, and the new `InMemoryLedgerClient` all call it; the locally-chaining clients (local + in-memory) then add `seq_id`/`prev_hash`/`entry_hash`. The bridge transport is structurally different (passes kwargs to the Rust extension, already redacts) and is left untouched.

**Tech Stack:** Python 3.10+, pytest, `src/ledger_spec.py` (`chain_hash`, `verify_chain`, `verify_dag`, `GENESIS_HASH`, `canonicalize`).

## Global Constraints

- Python **3.10+**. **No new runtime dependencies.**
- This is the **verifiable core** — the hash chain everything rests on. Touch `LocalJournalClient._append`'s chain logic (seq++/prev_hash/entry_hash) as little as possible; only its redact+content-ref+body-build region is replaced by `_build_body`.
- **Redact-before-content-ref is a security invariant** ("secrets never reach the shareable journal/blob store"). `_build_body` must redact BEFORE calling `_maybe_content_ref`.
- **Security behavior change (intended, must be flagged):** wiring `_build_body` into the HTTP `record_tool_call` makes that path redact for the first time. This is a fix; call it out in the commit and the final summary. It is safe even if the server also redacts (redact is idempotent on already-masked values).
- **Do NOT touch** the bridge transport (`KorgBridgeClient`) or the Rust path.
- **Do NOT migrate** `tests/test_concurrency.py`'s `_MemLedger` — its non-atomic `seq += 1` is the deliberate race-under-test; an atomic client would make that test prove nothing.
- "done" = tests + `ruff` + `mypy`. Run `pytest` on the touched suites each task.
- Repo: `/Users/clubpenguin/Documents/korg-ecosystem/korgex`, branch `ledger-inmemory-buildbody`.

## File Structure

- **Modify:** `src/korg_ledger.py` — add `_build_body` (near `_maybe_content_ref`, ~line 254); wire into `KorgLedgerClient.record_tool_call` (~418) and `LocalJournalClient._append` (~742); add `class InMemoryLedgerClient` (after `LocalJournalClient`, ~790).
- **Modify:** `tests/test_orchestrate.py` — replace its local `_MemLedger` with `InMemoryLedgerClient`; update `kind`→`tool_name` assertions; add a `verify_chain` assertion.
- **Create/extend:** `tests/test_ledger_buildbody.py` (Task 1 redaction-gap test) and `tests/test_inmemory_ledger.py` (Task 2 conformance + verify_chain).
- **Untouched:** `KorgBridgeClient`, `tests/test_concurrency.py`, `ledger_spec.py`.

### Reference — current shapes (verified)

```python
# HTTP KorgLedgerClient.record_tool_call (~418) — NO redact today (the gap):
payload_refs = []
safe_args = _maybe_content_ref(args, f"{tool_name}.args", payload_refs)
safe_result = _maybe_content_ref(result, f"{tool_name}.result", payload_refs)
body = {"schema_version": SCHEMA_VERSION, "source_agent": self.source_agent,
        "tool_name": tool_name, "args": safe_args, "result": safe_result,
        "payload_refs": payload_refs, "success": success, "duration_ms": duration_ms}
if triggered_by is not None: body["triggered_by"] = triggered_by
self._get_writer().enqueue(body)

# LocalJournalClient._append (~742) — redacts, then chains:
with self._lock:
    self._seq += 1; seq = self._seq
    payload_refs = []
    args = redact(args); result = redact(result)
    safe_args = _maybe_content_ref(args, f"{tool_name}.args", payload_refs)
    safe_result = _maybe_content_ref(result, f"{tool_name}.result", payload_refs)
    event = {"schema_version": SCHEMA_VERSION, "seq_id": seq, "source_agent": self.source_agent,
             "tool_name": tool_name, "args": safe_args, "result": safe_result,
             "payload_refs": payload_refs, "success": success, "duration_ms": duration_ms}
    if triggered_by is not None: event["triggered_by"] = triggered_by
    event["prev_hash"] = self._last_hash
    event["entry_hash"] = chain_hash(event, key=self._key)
    self._last_hash = event["entry_hash"]
    # ... write file ...

# helpers available: redact (src.sanitize), _maybe_content_ref, chain_hash(event, key=None),
#   GENESIS_HASH, SCHEMA_VERSION="1.0", _agent_identity(), verify_chain(events, key=None), verify_dag(events)
```

---

### Task 1: Extract `_build_body`; close the HTTP redaction gap

**Files:**
- Modify: `src/korg_ledger.py` (add `_build_body`; rewire HTTP `record_tool_call` + `LocalJournalClient._append`)
- Test: `tests/test_ledger_buildbody.py`

**Interfaces:**
- Produces: `_build_body(tool_name: str, args, result, success: bool, duration_ms: int, triggered_by: int | None, source_agent: str) -> tuple[dict, list[dict]]` — returns `(body, payload_refs)`. `body` has the 8 base fields (no `seq_id`/`prev_hash`/`entry_hash`) plus `triggered_by` when not None. Redacts BEFORE content-ref.

- [ ] **Step 1: Write the failing redaction-gap test**

```python
# tests/test_ledger_buildbody.py
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from src import korg_ledger as KL

def test_build_body_redacts_before_content_ref():
    # a fake AWS key shape must be masked in the assembled body
    body, refs = KL._build_body(
        "Bash", {"command": "echo AKIAIOSFODNN7EXAMPLE"}, {}, True, 0, None, "tester")
    assert "AKIAIOSFODNN7EXAMPLE" not in repr(body)
    assert body["tool_name"] == "Bash"
    assert body["success"] is True and "seq_id" not in body

def test_http_record_tool_call_redacts(monkeypatch):
    # the HTTP path historically skipped redaction; assert it no longer does.
    enqueued = {}
    c = KL.KorgLedgerClient.__new__(KL.KorgLedgerClient)
    c.source_agent = "tester"
    monkeypatch.setattr(c, "_is_available", lambda: True)
    class _W:
        def enqueue(self, body): enqueued.update(body)
    monkeypatch.setattr(c, "_get_writer", lambda: _W())
    c.record_tool_call("Bash", {"command": "x AKIAIOSFODNN7EXAMPLE y"}, {}, True, 0)
    assert "AKIAIOSFODNN7EXAMPLE" not in repr(enqueued)
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_ledger_buildbody.py -v`
Expected: FAIL — `_build_body` missing; `test_http_..._redacts` fails (secret leaks today).

- [ ] **Step 3: Add `_build_body` and rewire the two paths**

Add near `_maybe_content_ref`:

```python
def _build_body(tool_name, args, result, success, duration_ms, triggered_by, source_agent):
    """Assemble the common event body: redact (BEFORE blob-extraction — secrets must
    never reach the shareable journal/blob store), apply the 1 KB content-ref
    threshold, and return (body, payload_refs). Chain fields (seq_id/prev_hash/
    entry_hash) are added by the locally-chaining clients, not here."""
    payload_refs: list[dict] = []
    args = redact(args)
    result = redact(result)
    safe_args = _maybe_content_ref(args, f"{tool_name}.args", payload_refs)
    safe_result = _maybe_content_ref(result, f"{tool_name}.result", payload_refs)
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "source_agent": source_agent,
        "tool_name": tool_name, "args": safe_args, "result": safe_result,
        "payload_refs": payload_refs, "success": success, "duration_ms": duration_ms,
    }
    if triggered_by is not None:
        body["triggered_by"] = triggered_by
    return body, payload_refs
```

HTTP `record_tool_call` body (replace the inline redact-less build with):
```python
if not self._is_available():
    return
body, _refs = _build_body(tool_name, args, result, success, duration_ms,
                          triggered_by, self.source_agent)
self._get_writer().enqueue(body)
```

`LocalJournalClient._append` (replace the redact+content-ref+body region; KEEP the chain tail):
```python
with self._lock:
    self._seq += 1
    seq = self._seq
    event, _refs = _build_body(tool_name, args, result, success,
                               int(duration_ms), triggered_by, self.source_agent)
    event["seq_id"] = seq
    event["prev_hash"] = self._last_hash
    event["entry_hash"] = chain_hash(event, key=self._key)
    self._last_hash = event["entry_hash"]
    self.path.parent.mkdir(parents=True, exist_ok=True)
    with open(self.path, "a") as f:
        f.write(json.dumps(event) + "\n")
    return seq
```
Note: `seq_id` is now inserted AFTER the base body — confirm `chain_hash` canonicalizes by content regardless of dict insertion order (it uses `canonicalize`, which sorts keys). If the existing local-journal tests' frozen `entry_hash` vectors break, the order/þfields changed — investigate before forcing.

- [ ] **Step 4: Run — expect pass + no regression**

Run: `pytest tests/test_ledger_buildbody.py -v`
Then: `pytest tests/test_local_journal.py tests/test_korg_ledger.py tests/test_concurrency.py -q` (whichever exist — `ls tests | grep -E "ledger|journal|korg"` first).
Expected: PASS. If a frozen-hash vector test fails, STOP and report — the body shape must stay byte-identical for local.

- [ ] **Step 5: Commit**

```bash
git add src/korg_ledger.py tests/test_ledger_buildbody.py
git commit -m "refactor(ledger): extract _build_body; close redaction gap on the HTTP record_tool_call path"
```

---

### Task 2: `InMemoryLedgerClient` (chain-faithful) + conformance

**Files:**
- Modify: `src/korg_ledger.py` (add the class after `LocalJournalClient`)
- Test: `tests/test_inmemory_ledger.py`

**Interfaces:**
- Consumes: `_build_body` (Task 1), `chain_hash`, `GENESIS_HASH`, `verify_chain`, `verify_dag`.
- Produces: `InMemoryLedgerClient(source_agent: str | None = None, key: bytes | None = None)` with `.events: list[dict]`, and the 3-method protocol (`record_user_prompt(prompt, triggered_by=None) -> int`, `record_llm_call(**kw) -> int`, `record_tool_call(tool_name, args, result, success, duration_ms, triggered_by=None) -> int`). Each returns the assigned seq; `.events` passes `verify_chain` and `verify_dag`.

- [ ] **Step 1: Write the failing conformance + verify tests**

```python
# tests/test_inmemory_ledger.py
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from src import korg_ledger as KL
from src.ledger_spec import verify_chain, verify_dag

def test_record_then_verify_chain_no_io():
    c = KL.InMemoryLedgerClient(source_agent="tester")
    s1 = c.record_user_prompt("hello")
    s2 = c.record_tool_call("Bash", {"command": "ls"}, {"out": "x"}, True, 5, triggered_by=s1)
    assert (s1, s2) == (1, 2)
    assert verify_chain(c.events) == []      # byte-integrity
    assert verify_dag(c.events) == []        # causal structure

def test_tamper_is_detected():
    c = KL.InMemoryLedgerClient(source_agent="tester")
    c.record_tool_call("Bash", {"command": "ls"}, {}, True, 0)
    c.events[0]["args"] = {"command": "rm -rf /"}   # forge after the fact
    assert verify_chain(c.events) != []             # the chain catches it

def test_conformance_with_local_journal(tmp_path):
    # same input + same key + same source_agent => byte-identical event (minus the file)
    key = b"k" * 32
    mem = KL.InMemoryLedgerClient(source_agent="tester", key=key)
    loc = KL.LocalJournalClient(journal_path=str(tmp_path / "j.jsonl"), source_agent="tester")
    loc._key = key  # pin the key so hashes match
    mem.record_tool_call("Bash", {"command": "ls"}, {"out": "ok"}, True, 7)
    loc.record_tool_call("Bash", {"command": "ls"}, {"out": "ok"}, True, 7)
    import json
    disk = [json.loads(l) for l in (tmp_path / "j.jsonl").read_text().splitlines() if l.strip()]
    assert mem.events[0] == disk[0]   # identical: same redact/content-ref/body/chain
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_inmemory_ledger.py -v`
Expected: FAIL — `InMemoryLedgerClient` missing.

- [ ] **Step 3: Implement the class**

```python
class InMemoryLedgerClient:
    """Chain-faithful in-memory ledger: same redact/content-ref/body/hash-chain as
    LocalJournalClient, but appends to a list instead of a file. `.events` passes
    verify_chain (byte-integrity) AND verify_dag (causal structure) with no I/O.
    The canonical test double — record an event, then verify the chain. Atomic
    (locked), so it is NOT a stand-in for the deliberately-racy mock in
    tests/test_concurrency.py."""

    def __init__(self, source_agent: str | None = None, key: bytes | None = None) -> None:
        self.events: list[dict] = []
        self.source_agent = source_agent or _agent_identity()
        self._key = key
        self._seq = 0
        self._last_hash = GENESIS_HASH
        self._lock = threading.Lock()

    def _append(self, tool_name, args, result, success, duration_ms, triggered_by) -> int:
        with self._lock:
            self._seq += 1
            event, _refs = _build_body(tool_name, args, result, success,
                                       int(duration_ms), triggered_by, self.source_agent)
            event["seq_id"] = self._seq
            event["prev_hash"] = self._last_hash
            event["entry_hash"] = chain_hash(event, key=self._key)
            self._last_hash = event["entry_hash"]
            self.events.append(event)
            return self._seq

    def record_user_prompt(self, prompt: str, triggered_by: int | None = None) -> int:
        return self._append("user_prompt", {"prompt": prompt}, {}, True, 0, triggered_by)

    def record_llm_call(self, model="", prompt_tokens=0, completion_tokens=0, duration_ms=0,
                        triggered_by=None, **kw) -> int:
        return self._append("llm_inference", {"model": model, "prompt_tokens": prompt_tokens},
                            {"completion_tokens": completion_tokens}, True, duration_ms, triggered_by)

    def record_tool_call(self, tool_name, args, result, success, duration_ms,
                         triggered_by=None) -> int:
        return self._append(tool_name, args, result, success, duration_ms, triggered_by)
```
Note: if the conformance test fails because `LocalJournalClient._append` inserts `seq_id` in a different position than this class, that's fine — `chain_hash`/`canonicalize` sort keys, so order shouldn't matter. If `mem.events[0] != disk[0]`, diff the dicts and reconcile the field set (the two `_append`s must add exactly the same keys).

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_inmemory_ledger.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/korg_ledger.py tests/test_inmemory_ledger.py
git commit -m "feat(ledger): add chain-faithful InMemoryLedgerClient (record -> verify_chain, no I/O)"
```

---

### Task 3: Give `InMemoryLedgerClient` a real consumer — migrate `test_orchestrate.py`

**Files:**
- Modify: `tests/test_orchestrate.py` (delete its local `_MemLedger`; use `InMemoryLedgerClient`; update assertions; add a `verify_chain` assertion)

**Interfaces:**
- Consumes: `InMemoryLedgerClient` (Task 2). The orchestrate tests record via `record_user_prompt`/`record_tool_call`; the real events carry `tool_name` (e.g. `"user_prompt"`), `seq_id`, `triggered_by` — NOT the old mock's `kind` field.

- [ ] **Step 1: Read the current assertions**

Run: `grep -n "kind\|_MemLedger\|\.events\|verify_dag\|tool_name\|_SpyMem\|_OpaqueLedger" tests/test_orchestrate.py`
Map every `kind`-based assertion to its `tool_name` equivalent: a `kind == "user_prompt"` check becomes `tool_name == "user_prompt"`; a `kind == "tool"` check becomes the relevant `tool_name`. Keep `_SpyMem`/`_OpaqueLedger` if they exercise non-`.events` paths — only replace the plain `_MemLedger` stand-in. If a test depends on the mock's exact minimal shape in a way that doesn't translate, STOP and report it rather than weakening the assertion.

- [ ] **Step 2: Migrate**

Replace `inner = _MemLedger()` with `inner = KL.InMemoryLedgerClient(source_agent="orch-test")` (import `from src import korg_ledger as KL`). Update each `kind` assertion to `tool_name`. Delete the local `_MemLedger` class if nothing else uses it. Add one strengthening assertion to the main DAG test:
```python
from src.ledger_spec import verify_chain
assert verify_chain(inner.events) == []   # the orchestration DAG is now byte-verifiable too
```

- [ ] **Step 3: Run — expect pass**

Run: `pytest tests/test_orchestrate.py -v`
Expected: PASS, with the new `verify_chain` assertion green. Investigate any failure as a real shape mismatch (the migration's whole point is to test against the REAL event shape).

- [ ] **Step 4: Confirm the concurrency mock was left alone**

Run: `grep -n "_MemLedger" tests/test_concurrency.py` → expect it STILL present (deliberately racy; must not be migrated).

- [ ] **Step 5: Commit**

```bash
git add tests/test_orchestrate.py
git commit -m "test(orchestrate): use InMemoryLedgerClient (real consumer) + assert verify_chain on the DAG"
```

---

## Self-Review

**Scope coverage:** `_build_body` extraction + HTTP redaction-gap fix (Task 1); chain-faithful `InMemoryLedgerClient` with conformance + tamper + verify_chain tests (Task 2); a real consumer so the client isn't speculative by the deletion test (Task 3). Bridge untouched; `test_concurrency` racy mock untouched (flagged twice).

**Risk register:** the only edit to the chain logic is `LocalJournalClient._append` (Task 1 Step 3), and the conformance test (Task 2) + any frozen-hash vector tests guard that the local event stays byte-identical. The HTTP redaction change is intended and flagged.

**Type consistency:** `_build_body(...) -> (body, payload_refs)` used identically in Tasks 1–2. `InMemoryLedgerClient` mirrors `LocalJournalClient`'s 3-method protocol and return-seq contract.

**Out of scope (noted, not dropped):** no `record_event` rename / 68-site migration (rejected as churn on the verifiable core); bridge stays a separate maintenance point (Rust schema); `record_user_prompt`/`record_llm_call` HTTP paths already redact via `_post_sync` and are left as-is.
