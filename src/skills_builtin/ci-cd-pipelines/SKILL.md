---
name: ci-cd-pipelines
description: Build automated pipelines that gate merges and ship reliably
version: 1.0
trust: built-in
---

CI/CD makes quality automatic: every change is built, tested, and (when trusted)
shipped the same way, every time.

1. **CI gates merges.** On every push/PR, run: install → lint → build → test. The
   pipeline is the source of truth for "is this mergeable" — keep it green and treat
   a red build as stop-the-line.
2. **Make it fast and deterministic.** Cache dependencies; parallelize independent
   jobs; pin tool versions so runs are reproducible. A slow or flaky pipeline gets ignored.
3. **No flaky gates.** A test that fails intermittently erodes trust in the whole
   pipeline — fix or quarantine it (see condition-based-waiting for the usual cause).
4. **CD ships the artifact CI built** — don't rebuild for deploy. Promote the exact
   tested artifact through environments (e.g. staging → prod).
5. **Secrets via the CI secret store**, never in the config or logs. Scope them to
   the jobs that need them.
6. **Match the project.** Use its existing CI system and config conventions; add a
   job, don't introduce a second CI tool.
7. Surface results where people see them (status checks, required checks on the
   default branch) so the gate actually gates.

Pair with deploying-safely for what happens after the pipeline goes green.
