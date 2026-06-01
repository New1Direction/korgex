---
name: verify-before-done
description: Prove the work is actually complete before claiming it — never report unverified
version: 1.0
trust: built-in
---

The most common failure is claiming done while something is broken or unfinished.
korgex records everything to a verifiable ledger — match that standard: report
only what you've checked.

1. **Re-read your own change.** Read the files you edited back; confirm the edit
   applied as intended and nothing adjacent broke.
2. **Run the checks.** Run the relevant tests / build / linter. Quote the actual
   result. If tests fail, say so with the output — do not claim success.
3. **Check the checklist.** If you used `TaskCreate`, confirm every item is
   `completed`. Open items mean you're not done.
4. **Match the request.** Re-read the original ask. Did you do all of it, or only
   the easy part? Surface anything you skipped or couldn't do.
5. **Report honestly.** State plainly what's done and verified, what's untested,
   and what's left. If a step was skipped, say so. No hedging on verified work; no
   confidence on unverified work.
