---
name: issue-triage
description: Turn a vague report into a clear, reproducible, prioritized issue
version: 1.0
trust: built-in
---

A good issue is half the fix. Whether filing or triaging, drive toward a crisp,
reproducible statement of the problem.

1. **Reproduce or get repro steps.** The most valuable thing in any bug report is
   a minimal, deterministic way to trigger it. If you can't reproduce it, say what
   you tried and what info is still needed — don't guess at a fix.
2. **Capture the essentials:** exact steps, expected vs actual, environment
   (version, OS, config), and the actual error/output (not a paraphrase).
3. **Classify:** is it a bug, a feature request, a question, or a duplicate? Search
   existing issues first — link duplicates instead of forking the discussion.
4. **Prioritize by impact × frequency.** Severity (data loss / crash / cosmetic) and
   how many users hit it. Be honest; not everything is P0.
5. **Make it actionable.** A triaged issue should tell the implementer where to look
   and how to know it's fixed (the repro becomes the acceptance test).
6. **For a fix you implement,** reference the issue, and add a regression test from
   its repro so it can't silently come back (see test-driven-development).

Red flag: "it's broken" with no repro, version, or expected behaviour — that's not
yet an issue, it's a prompt to gather more.
