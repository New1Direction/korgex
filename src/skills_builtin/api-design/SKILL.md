---
name: api-design
description: Design interfaces that are clear, hard to misuse, and cheap to change
version: 1.0
trust: built-in
---

An API (a function, module, endpoint, or library surface) is a contract others
depend on. Design the smallest, clearest one that does the job.

1. **Start from the caller.** Write the usage you wish existed first. If it reads
   well and the obvious call is the correct one, the design is good.
2. **Make it hard to misuse.** Prefer types/enums over stringly-typed flags; make
   illegal states unrepresentable; require the essential args, default the rest.
   The easy path should be the safe path.
3. **Keep the surface small.** Expose the minimum; hide internals. Every public
   name is a future maintenance promise. Fewer, well-named entry points beat many
   overlapping ones.
4. **Be consistent.** Match the naming, argument order, and error conventions of
   the surrounding code so callers can predict it.
5. **Define the contract:** inputs, outputs, errors/failure modes, and side effects.
   Decide how errors surface (exception vs result) and be uniform.
6. **Design for change.** Additive evolution (new optional params, new functions)
   over breaking signatures. If you must break, version it and migrate (see
   database-migrations / breaking-change discipline).
7. **YAGNI.** Don't add parameters or extension points "just in case" — they're
   surface you'll maintain forever. Add them when a real caller needs them.
