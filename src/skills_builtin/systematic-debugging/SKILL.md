---
name: systematic-debugging
description: Reproduce, isolate the root cause, fix the cause not the symptom, then prove it
version: 1.0
trust: built-in
---

Resist the urge to guess-and-patch. A bug you can't reproduce isn't fixed.

1. **Reproduce reliably.** Find the smallest, deterministic way to trigger it.
   Capture the exact error/output. If you can't reproduce it, gather more signal
   before changing anything.
2. **Locate it.** Read the actual error and stack. Use Grep/Glob to find the code
   on the path; read the surrounding code and its callers. Form ONE hypothesis at
   a time about the root cause.
3. **Test the hypothesis.** Add a focused assertion, log, or a failing test that
   would be true only if your hypothesis holds. Confirm before fixing.
4. **Fix the cause.** Change the root cause, not the symptom. Avoid masking it
   with a catch-all or a retry. Keep the change minimal.
5. **Prove it.** Add a regression test that fails before your fix and passes after
   (see test-driven-development). Re-run the full suite; confirm nothing else broke.
6. State what the cause was and why the fix addresses it.
