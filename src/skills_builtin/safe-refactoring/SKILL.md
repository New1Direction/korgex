---
name: safe-refactoring
description: Change code structure without changing behaviour — tests green at every step
version: 1.0
trust: built-in
---

Refactoring is improving structure while behaviour stays identical. If behaviour
changes, that's a feature/fix — do it separately, with its own test.

1. **Pin behaviour first.** Make sure the code is covered by tests. If it isn't,
   add characterization tests that capture current behaviour *before* you touch it.
2. **One transformation at a time.** Rename, extract, inline, move — a single kind
   of change per step. Don't mix a rename with a logic tweak.
3. **Stay green.** Run the tests after each step. If they go red, the step changed
   behaviour — revert it and redo it smaller.
4. **Don't expand scope.** Resist "while I'm here" rewrites. Keep the diff about the
   one structural improvement you set out to make.
5. **Commit in small steps** so each is independently revertable.

Red flags: tests you had to *change* to stay green (you altered behaviour); a giant
diff touching unrelated code; no tests before you started.
