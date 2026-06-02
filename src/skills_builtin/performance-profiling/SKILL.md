---
name: performance-profiling
description: Make it fast by measuring first — never optimize on a guess
version: 1.0
trust: built-in
---

The cardinal rule: measure before you optimize, and measure again after. Intuition
about what's slow is usually wrong.

1. **Define "fast enough".** What's the target (latency, throughput, memory) and on
   what input? Without a target you can't know when to stop.
2. **Reproduce + measure the baseline.** Get a repeatable workload and time it.
   Record the number — it's what you'll compare against.
3. **Profile to find the real hotspot.** Use a profiler / timing instrumentation,
   not eyeballing. Find where the time/allocations actually go. The bottleneck is
   often not where you'd guess (it's frequently I/O, N+1 queries, or an accidental
   O(n²), not "slow code").
4. **Fix the biggest cost first.** Often algorithmic (data structure, caching,
   batching) beats micro-optimization. Change one thing.
5. **Re-measure.** Confirm the change actually helped against the baseline. Keep it
   only if it did; revert if it didn't.
6. **Guard it.** For a critical path, add a benchmark/regression check so it can't
   silently regress later.

Red flags: optimizing without a measurement, micro-tuning a cold path, trading
readability for speed that doesn't move the target number.
