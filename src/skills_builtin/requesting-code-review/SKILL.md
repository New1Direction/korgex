---
name: requesting-code-review
description: Get a fresh-eyes review of your own change before calling it done
version: 1.0
trust: built-in
---

You are biased toward your own work — you test what you built, not what's required.
A fresh-eyes pass catches what you can't see. Do this before finalizing anything
non-trivial.

1. **Prepare the change.** Make sure it's coherent: `git diff` is clean, tests pass.
2. **Dispatch a reviewer with NO prior context.** Spawn a subagent (the `Agent`
   tool) and give it ONLY: the diff, the original requirement, and the instruction
   to review for correctness, edge cases, security, missing tests, and clarity —
   and to try to REFUTE that the change is correct. Don't tell it your conclusions.
3. **For high-stakes work, use more than one** reviewer with different lenses
   (correctness / security / does-it-actually-meet-the-requirement). Majority skepticism wins.
4. **Act on the findings.** Fix real issues. If you disagree, verify with code, not
   assertion. Distinguish blockers from nits.
5. **Re-verify** after fixes (see verify-before-done), then report what review found
   and what you changed.
