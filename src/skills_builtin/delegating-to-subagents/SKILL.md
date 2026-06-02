---
name: delegating-to-subagents
description: Split independent work across parallel subagents with focused scope, then aggregate
version: 1.0
trust: built-in
---

When a task has independent parts, delegate them to subagents (the `Agent` tool)
instead of doing everything in one context. Each gets an isolated context window,
so you stay focused on coordination.

1. **Decide if it fits.** Use subagents when parts are INDEPENDENT (different
   files/subsystems/bugs) and can run without each other's intermediate state.
   If they share tightly-coupled state, do it inline instead.
2. **Scope each one tightly.** Give each subagent: a single clear goal, the exact
   context it needs (don't assume it sees this conversation), constraints ("don't
   touch X"), and what to return. Narrow scope = reliable result.
3. **Dispatch in parallel** for independent work; sequential only when one depends
   on another's output.
4. **Aggregate.** Read each result, reconcile conflicts, and verify the combined
   result yourself — subagents can make systematic mistakes. Then run the suite.
5. Keep the user's view current: reflect delegated work in your task checklist.
