---
name: writing-a-plan
description: Before multi-step work, explore the code then lay out a tracked checklist
version: 1.0
trust: built-in
---

Plan before you build so you don't drift or thrash. Scale the plan to the task —
a one-liner needs no plan; a feature does.

1. **Explore first.** Read the README/AGENTS.md and the files you'll touch, plus
   their callers. Understand the existing patterns before proposing changes.
2. **Decompose.** Break the work into discrete, verifiable steps in dependency
   order. Each step should be something you can finish and check.
3. **Record the checklist.** Call `TaskCreate` with the steps. This shows the user
   your plan and is fed back to you each turn so you work through it.
4. **Execute one step at a time.** Mark a step `in_progress` with `TaskUpdate` when
   you start it and `completed` the moment it's done — one at a time, not batched.
5. **Adapt.** If the plan turns out wrong, re-run `TaskCreate` with the revised
   steps rather than silently abandoning it.
6. Don't claim the task is finished while any item is still open.
