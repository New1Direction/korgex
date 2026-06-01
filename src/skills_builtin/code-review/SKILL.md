---
name: code-review
description: Review a change for correctness, edge cases, security, tests, and clarity
version: 1.0
trust: built-in
---

Review the DIFF, but read enough surrounding code to judge it in context.

1. **Get the diff.** `git diff` (or the relevant range). Read each hunk with its
   surrounding function and callers — a change is only correct in context.
2. **Check, in priority order:**
   - **Correctness:** does it do what it claims? Off-by-one, null/empty, error paths.
   - **Edge cases:** boundaries, concurrency, large/empty inputs, failure modes.
   - **Security:** injection (command/SQL/XSS), path traversal, secrets in code,
     unvalidated input crossing a trust boundary.
   - **Tests:** is the new behaviour covered? Would a test have caught a regression?
   - **Clarity:** names, dead code, needless complexity, comments that explain WHY.
3. **Verify, don't assume.** If unsure a branch is reachable or a value can be
   null, trace it or write a quick check rather than guessing.
4. **Report findings ranked** by severity (blocker → nit), each with file:line and
   a concrete suggested fix. Call out what's GOOD too. Don't invent issues to seem thorough.
