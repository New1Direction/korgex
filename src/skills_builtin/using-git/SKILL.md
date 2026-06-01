---
name: using-git
description: Clean atomic commits, honest messages, and a safe branch/PR workflow
version: 1.0
trust: built-in
---

Treat git history as a record someone will read later. Commit and push only when
the user asks; if you're on the default branch, branch first.

**Committing**
1. Review what you're about to commit (`git status`, `git diff --staged`). Stage
   only related changes — one logical change per commit. Don't `git add -A` blindly.
2. Write a message that says WHAT changed and WHY. First line ≤ ~72 chars,
   imperative ("Fix …", "Add …"); body for the why if non-obvious.
3. Never commit secrets, large artifacts, or unrelated reformatting.

**Branches & PRs**
4. Don't commit straight to the default branch — create a focused branch.
5. Open a PR with a clear title and a body covering: what changed, why, how it was
   tested. Confirm with the user before pushing or opening it (it's outward-facing).

**Safety**
6. Never force-push a shared branch, hard-reset away uncommitted work, or rewrite
   published history without explicit confirmation. Prefer revert over reset for
   anything already pushed.
