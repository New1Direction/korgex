---
name: deploying-safely
description: Ship to production with staged rollout, health checks, and a fast rollback
version: 1.0
trust: built-in
---

A deploy is an outward-facing, hard-to-undo action. The goal is to ship often AND
make a bad release boring to recover from. Confirm before deploying unless the user
has clearly authorized it.

1. **Ship the tested artifact.** Deploy the exact build CI verified — don't rebuild
   or hand-tweak on the way out.
2. **Roll out gradually.** Prefer canary / staged / blue-green over flipping 100% at
   once. Watch the new version's health (errors, latency) before widening.
3. **Have a rollback ready BEFORE you ship.** Know the one command/step to revert,
   and that the previous version still works. Rollback should be faster than forward-fix.
4. **Decouple deploy from release.** Use feature flags so you can deploy code dark
   and turn behaviour on/off without a redeploy — and kill it instantly if it misbehaves.
5. **Backwards-compatible steps.** Don't ship a code change that requires a not-yet-run
   migration, or drop something the old version still uses (see database-migrations'
   expand/contract).
6. **Verify after.** Check health/metrics post-deploy; don't declare success on
   "it deployed" — declare it on "it's serving correctly" (see verify-before-done).
7. If anything looks wrong, **roll back first, diagnose second** (see incident-response).
