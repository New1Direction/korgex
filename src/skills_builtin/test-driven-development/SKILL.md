---
name: test-driven-development
description: Write a failing test first, watch it fail, then write the minimal code to pass
version: 1.0
trust: built-in
---

Use this whenever you add or change behaviour. The discipline is what makes the
test trustworthy — a test you never saw fail proves nothing.

1. **RED — write one failing test.** Pick the smallest next behaviour. Write a
   test that asserts it, using real code (no mocks unless a dependency is truly
   unavailable). One behaviour per test; name it for the behaviour.
2. **Watch it fail.** Run it. Confirm it fails for the *right reason* (the feature
   is missing), not a typo or import error. If it passes already, you're testing
   existing behaviour — fix the test.
3. **GREEN — minimal code.** Write the least code that makes it pass. Don't add
   options, abstractions, or features the test doesn't demand.
4. **Watch it pass.** Run it; confirm the whole suite stays green and the output
   is clean (no new warnings).
5. **REFACTOR.** Improve names and remove duplication while green. Don't add
   behaviour here.
6. Repeat for the next behaviour.

If a test is hard to write, the design is usually too coupled — simplify the
interface before continuing.
