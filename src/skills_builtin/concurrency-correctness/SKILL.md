---
name: concurrency-correctness
description: Reason about shared state, races, and ordering before writing concurrent code
version: 1.0
trust: built-in
---

Concurrency bugs are nondeterministic and brutal to debug — get the design right up
front rather than patching symptoms.

1. **Find the shared mutable state.** Races happen only where two flows touch the
   same mutable data. Enumerate it. The safest design shares nothing — pass
   messages / immutable data instead.
2. **Protect every access, consistently.** If state is shared, guard ALL reads and
   writes with the same lock (or use an atomic/concurrent structure). A single
   unguarded access defeats the lock.
3. **Avoid deadlock.** Acquire multiple locks in a consistent global order; hold
   them as briefly as possible; don't call out to unknown code while holding one.
4. **Don't assume ordering or atomicity.** "It worked when I ran it" proves nothing
   — interleavings you didn't see still happen. `check-then-act` is a race unless
   atomic.
5. **Wait on conditions, not sleeps** (see condition-based-waiting). For tests of
   concurrent code, synchronize on events/barriers — never `sleep` to dodge a race.
6. **Bound and clean up.** Cap parallelism; ensure every spawned task is awaited or
   cancelled; propagate errors from workers instead of losing them.

Red flags: a `sleep` "fixing" flakiness, a lock around some-but-not-all accesses,
shared mutable state with no guard, swallowed worker exceptions.
