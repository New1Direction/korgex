---
name: exploring-a-codebase
description: Map an unfamiliar codebase and find the right place to change before editing
version: 1.0
trust: built-in
---

Don't edit code you haven't located in context. A few minutes mapping prevents
editing the wrong file or breaking a caller you didn't know about.

1. **Orient.** Read README/AGENTS.md, the project config (pyproject/package.json/
   Cargo.toml), and the directory layout. Learn how it's built, tested, and run.
2. **Find the entrypoints.** Locate where execution starts and the main modules.
   `Glob` for structure, `Grep` for symbols.
3. **Trace to the target.** For a change, find the exact code that owns the
   behaviour — search by symbol/string, read the function AND its callers. Confirm
   you've found the real source, not a generated/duplicated copy.
4. **Learn the conventions.** Note the existing patterns (naming, error handling,
   test layout, comment density) so your change reads like the surrounding code.
5. **For broad exploration, delegate.** If mapping spans many files, dispatch a
   subagent (see delegating-to-subagents) to sweep and report, keeping your context
   focused.
6. Only then edit — and read a file fully before you Edit it.
