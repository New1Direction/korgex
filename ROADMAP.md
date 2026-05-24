# Korgex — Next Frontier Roadmap

## Release Sequence

| Version | Status | Scope |
|---------|--------|-------|
| **v0.2.x** | ✅ Shipped | MCP client, multi-model routing, streaming TUI, FastAPI dashboard, VS Code sidecar, 27 tests |
| **v0.3.0** | 🔲 Next | korg-bridge (PyO3) — wires Korgex agent loop into Korg's Rust WAL; optional ledger log |
| **v0.4.0** | 🔲 Planned | KorgChat alpha — first consumer chat product, built on connected korg-bridge stack from day one |

v0.3.0 is the bridge work. Do not build more Python-side features until the PyO3 bindings exist — KorgChat must be designed assuming the bridge is live, never retrofitted onto a stateless loop.

---

## 1. 🔄 Test-Driven Self-Healing Loops (Inner-Loop TDD)

**Status:** ❌ Not started  
**Priority:** Critical  
**Dependencies:** `run_in_bash_session`, `diff_engine.py`

### The Concept
When `run_in_bash_session` executes a test suite and returns a failure, Korgex enters an intense, high-speed **Self-Correction Inner Loop** instead of exiting or asking the user.

### How It Works
1. Agent parses the traceback error message
2. Isolates the exact failed lines using AST diffing
3. Triggers a small, ultra-fast local model (e.g., Llama-3-8B) inside the sandbox
4. Iteratively patches, reruns tests, and self-corrects until tests pass

### Acceptance Criteria
- [ ] Parse traceback → extract failed function/line
- [ ] AST diff isolates the exact code region
- [ ] Local model generates candidate patches
- [ ] Patch → rerun → loop until green
- [ ] Parent agent only delivers code after empirical verification

### Architecture
```
Test Failure → Parse Traceback → AST Diff Isolation
    → Llama-3-8B generates patch → Rerun tests
    → Pass? → Deliver to user
    → Fail? → Loop (max 5 iterations)
    → Max iterations reached? → Report to user
```

---

## 2. 📊 Dependency Graph Impact Analysis (No-Break Commit Safeguard)

**Status:** ❌ Not started  
**Priority:** High  
**Dependencies:** `graphify` integration or custom static analysis

### The Concept
Before editing a file, Korgex understands the cascading impacts of its changes across the entire repository.

### How It Works
1. Build a lightweight static analysis compiler that indexes import hierarchies and function calls
2. When the agent plans to edit a function signature, trigger a tool that lists all dependent files
3. Force the agent to update all downstream call sites in the same commit

### Acceptance Criteria
- [ ] Build import/export graph of the codebase
- [ ] Index function call sites across files
- [ ] Tool: `get_dependents(filepath, symbol_name)` → list of files
- [ ] Agent must patch all downstream callers before committing
- [ ] Prevent "fixed one file, broke four others" scenarios

### Architecture
```
Index Codebase (imports, function defs, call sites)
    → Agent plans to edit `foo()`
    → get_dependents("src/lib.py", "foo")
        → ["src/api.py", "tests/test_lib.py", "src/cli.py"]
    → Agent must update ALL of them in one commit
```

---

## 3. ⚡ Automated Performance Profiling (No-Regression Guarantee)

**Status:** ✅ Shipped (v0.2.x) — wired via dashboard `/api/swarm/profile`  
**Priority:** Medium  
**Dependencies:** `sandbox.py`, `cProfile`, `node --cpu-prof`

### The Concept
Korgex verifies that its code changes are not just correct, but highly performant.

### How It Works
1. Run the test suite under CPU/memory profilers
2. Profile feature branch and main branch
3. Compare metrics: execution time, memory footprint, database query counts
4. Flag performance regressions (O(n²) algorithms, N+1 queries)
5. Self-correct before requesting PR merge

### Acceptance Criteria
- [ ] Profile Python with `cProfile` / `pytest-benchmark`
- [ ] Profile Node.js with `--cpu-prof` / `--heap-prof`
- [ ] Tool: `profile_code(filepath)` → metrics dict
- [ ] Tool: `compare_performance(branch_a, branch_b)` → diff report
- [ ] Auto-flag regressions > 10%
- [ ] Self-correct slow code before submission

---

## 4. 📦 AST-Based Context Compression (For Massive Codebases)

**Status:** ❌ Not started  
**Priority:** Medium  
**Dependencies:** Tree-sitter or Python `ast` module

### The Concept
In massive legacy codebases, feeding entire 2,000-line files to the LLM consumes context tokens and dilutes focus.

### How It Works
1. Implement an AST Minimizer that presents a "skeleton" of the file
2. Fully show the target method
3. Prune/fold bodies of unrelated methods/classes into single-line comments
4. Allows working in million-line repos without exhausting token limits

### Acceptance Criteria
- [ ] Parse file into AST
- [ ] Identify target function/method
- [ ] Expand target fully, collapse unrelated into `# ... (43 lines)`
- [ ] Tool: `read_file_compressed(filepath, target_symbol)` → skeleton
- [ ] Works for Python, JavaScript, TypeScript, Go, Rust
- [ ] Reduce token cost by 60%+ for large files

### Architecture
```
read_file_compressed("api.py", "handle_login")
    → Shows full handle_login() with body
    → Collapses other functions: 
        # def validate_email(email): (... 12 lines)
        # class UserService: (... 87 lines)
        # def send_welcome_email(user): (... 24 lines)
    → ~80% fewer tokens than full read_file
```

---

## 5. 🧩 VS Code / Cursor Sidecar Extension

**Status:** ✅ Shipped (v0.2.x) — 4 commands wired, port aligned to 8090  
**Priority:** Low (nice-to-have)  
**Dependencies:** `src/dashboard.py`, WebSocket protocol

### The Concept
Bridge the gap between the Korgex dashboard and the developer's IDE.

### How It Works
1. Build a VS Code extension that syncs with the FastAPI dashboard via WebSockets
2. Developers view active plans, real-time diffs directly in their editor sidebar
3. Click "Approve" or type steer instructions without leaving the IDE

### Acceptance Criteria
- [ ] VS Code extension scaffold (package.json, activation)
- [ ] WebSocket client connects to dashboard
- [ ] Sidebar panel shows: current plan, logs, approval button
- [ ] Inline diff view in editor
- [ ] Cursor extension (TypeScript) compatibility

### Architecture
```
VS Code Extension (WebSocket Client)
    ↔ FastAPI Dashboard (WebSocket Server)
        ↔ Korgex Agent Loop
```

---

## Progress

| # | Feature | Status | Priority | Est. Effort |
|---|---------|--------|----------|-------------|
| 1 | Self-Healing TDD Loops | 🟡 Module exists, not wired into auto-trigger | High | 1 day |
| 2 | Dependency Graph Analysis | 🟡 Module exists, not bridged into USER_TOOLS | Medium | 2 hr |
| 3 | Performance Profiling | ✅ Shipped v0.2.x | — | — |
| 4 | AST Context Compression | 🟡 Module exists, not bridged into USER_TOOLS | Medium | 2 hr |
| 5 | VS Code Sidecar Extension | ✅ Shipped v0.2.x | — | — |

---

## Appendix: AlphaEvolve takeaways (2026-05-24 reading)

Read DeepMind's AlphaEvolve paper (44 pages). It's a **batch evolutionary coding agent** — different paradigm from korgex (interactive, single-task, latency-optimized). The two are complementary, not competing. Filtered against staying true to korgex's vision, here's what's extractable.

### Validations (already aligned, no work needed)

- **SEARCH/REPLACE diff format** — DeepMind picked the same format korgex uses for Edit. Document it as an industry-standard convention.
- **Plan-first prompting + rich context** — paper's ablations show "no context" performs significantly worse. Validates our SYSTEM_PROMPT directives.
- **Mixed-capability ensemble (Flash + Pro)** — paper uses Gemini Flash for throughput + Pro for breakthroughs. korgex's `--mode plan/execute/debug` does the same on a per-task basis.

### Small wins (worth shipping — under a day each)

- **`--judge MODEL` flag** — for soft criteria ("more readable", "more idiomatic"), spawn a separate scoring call. AlphaEvolve's "LLM-generated feedback" pattern. Useful for review/refactor flows.
- **Evaluation cascade in the system prompt** — explicit directive: "verify cheapest checks first (syntax/typecheck), then linter, then unit tests, then integration." Already implicit; making it explicit improves reliability on long tasks.
- **`--ensemble N` flag** — spawn N parallel agents on the same task, return all diffs, user picks winner. Quick win for hard problems. Cost: N× tokens per run.

### Deferred (worth doing later as opt-in features, not core)

- **`korgex evolve <file> --metric "<cmd>" --iterations N`** — full AlphaEvolve-lite. EVOLVE-BLOCK markers in source, fitness-function command, N iterations, best-scoring child wins. Narrow use case (perf optimization, algorithm improvement) but distinctive — neither Cursor nor Claude Code has this.
- **Meta-prompt evolution** — let the LLM rewrite parts of its own system prompt based on what worked across runs. Research-level; revisit when we have usage data.
- **Multi-objective scoring** — paper's counterintuitive finding: optimizing for multiple metrics improves single-metric performance, because the diverse exemplars in prompts produce more varied candidates. Worth experimenting once we have evolve mode.

### Vision mismatch (do not pursue)

- **Distributed asyncio pipeline** — paper optimizes throughput across an evaluation cluster. korgex optimizes single-user latency. Wrong axis.
- **Program database / MAP-elites / island populations** — the evolutionary memory store. Only makes sense inside `korgex evolve`, not the main loop.
- **Algorithm discovery as a goal** — AlphaEvolve found a 48-multiplication 4×4 complex matmul algorithm after 56 years of human work. That's a search problem, not a coding-task problem. korgex shouldn't try to compete here.

### Strategic note

AlphaEvolve and korgex sit at different points on the same axis:

```
  korgex                                         AlphaEvolve
  ↓                                              ↓
  one task, one shot, ship it ←———————————→ thousands of samples, search to optimum
  interactive, low-latency                       batch, throughput-optimized
  generic coding (most problems)                 metric-gradable problems (rare but valuable)
```

The future `korgex evolve` subcommand would slide korgex one notch toward AlphaEvolve for the narrow class of problems where it makes sense, without changing the core interactive identity. If we ever do this, the natural persistence layer is **korg's signed cognitive ledger** — that's where a real korgex+korg integration becomes interesting.