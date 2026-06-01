---
name: spike
description: Throwaway exploration to learn the unknown, then delete it and build it properly
version: 1.0
trust: built-in
---

A spike is a time-boxed experiment to answer a question ("does this API work like
I think?", "is this approach viable?"). Its only output is knowledge — the code is
disposable.

1. **State the question.** Write down exactly what you're trying to learn. The
   spike is done when it's answered, not when the code "works".
2. **Go fast and dirty.** Skip tests, error handling, and polish. Hardcode. The
   point is to learn, not to ship — don't invest in code you'll throw away.
3. **Time-box it.** Set a bound. If you blow past it, you've learned the problem is
   harder than expected — that's itself an answer; report it.
4. **Capture the finding.** Record what you learned (and consider saving it as a
   skill or memory if it'll recur).
5. **THROW THE CODE AWAY.** Delete the spike. Then build the real thing from
   scratch with test-driven-development — do NOT "clean up" the spike into
   production code; it carries hidden assumptions and skipped cases.

Red flag: a spike quietly becoming the implementation. Delete means delete.
