---
name: error-handling
description: Handle failures deliberately — fail loud, recover narrow, never swallow
version: 1.0
trust: built-in
---

Good error handling makes failures visible and recoverable. The worst bug is the
one a bare `except` hides.

1. **Decide per failure: recover, propagate, or fail.** Only handle an error where
   you can actually do something useful about it. Otherwise let it propagate to a
   layer that can.
2. **Catch narrowly.** Catch the specific exception you expect, not a blanket
   catch-all. A broad `except` hides real bugs and turns a crash into silent
   corruption.
3. **Never swallow.** If you catch, either handle it meaningfully or re-raise with
   context. Don't `except: pass` over something that matters. Empty handlers are
   only OK for genuinely-optional, best-effort side work.
4. **Preserve context.** Include what you were doing and the inputs in the message;
   chain the original cause (don't discard the traceback). The message should tell
   the next reader how to fix it.
5. **Fail fast on programmer errors** (bad state, broken invariants) — crash loudly.
   Handle gracefully only *expected* operational errors (network, missing file,
   bad user input).
6. **Clean up reliably** — release resources in finally / context managers / defer,
   on both the success and failure paths.
7. Test the failure paths, not just the happy path.
