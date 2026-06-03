---
name: security-scan
description: Run a verifiable security scan (vulns, secrets, misconfig) over code you wrote or deps you added, before calling the work done
version: 1.0
trust: built-in
---

# Verifiable security scan

When you've added or changed dependencies, written code that touches auth, secrets,
subprocess calls, file I/O, or the network — or the user asks "is this secure?" — run a
security scan before you finish.

## How

Use the `security_scan` tool. It wraps the best scanner on the machine and is
read-only (never modifies files):

- **trivy** if installed — vulnerabilities, leaked secrets, IaC misconfig, licenses.
- otherwise **pip-audit** (Python dependency CVEs) or **bandit** (insecure Python patterns).

Calls:

- `security_scan` with no args scans the project root.
- pass `path` to scan a subtree, or `scanner` to force `trivy` / `pip-audit` / `bandit`.

For the user (or in CI) the same scan is `korgex scan [path]` — it exits nonzero when a
high/critical finding is present, so it gates a pipeline.

## What you get

Each finding has `kind` (vuln | secret | misconfig | license), `severity`
(critical → low), `id` (CVE / rule), `target` (file or `pkg@version`), and a `fix` when
the scanner knows one. The scan is recorded to the verifiable ledger, so findings are
tamper-evident and traceable: `korgex why <CVE-or-file>` walks a finding back to the
prompt that introduced it, and `korgex verify` proves the report wasn't edited.

## Acting on findings

- **Critical / high:** fix before finishing — bump the dependency to its `fix`
  version, remove and rotate the leaked secret, correct the misconfig — then re-scan
  to confirm it's gone.
- **False positive:** say so explicitly and explain why; don't silently drop it.
- **A surfaced secret is compromised.** Never commit it; tell the user to rotate it.
