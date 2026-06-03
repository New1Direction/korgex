---
description: Review code — local uncommitted changes, or a GitHub PR (pass a PR number/URL)
argument-hint: [pr-number | pr-url | blank for local]
---
<!-- Adapted from the ECC project (github.com/affaan-m/ECC), MIT-licensed. -->
# Code Review

**Input:** $ARGUMENTS

If `$ARGUMENTS` names a PR (a number or URL), review that PR; otherwise review the local uncommitted changes.

## 1 — Gather
- **Local:** `git status`, then `git diff` (staged + unstaged).
- **PR:** `gh pr diff $ARGUMENTS` for the changes and `gh pr view $ARGUMENTS` for the description.

## 2 — Review for
- **Correctness** — logic errors, unhandled edge cases, error paths.
- **Security** — injection, secrets committed in code, missing authorization, unsafe input handling.
- **Tests** — are the changes covered? which cases are missing?
- **Clarity** — naming, dead code, comments that match the surrounding style.

## 3 — Report
Group findings by severity — **blocker / should-fix / nit** — each with a `file:line` and a concrete fix. End with a one-line verdict (ship, or changes needed).
