---
name: react-doctor
description: Catch bad React in your changes — run react-doctor's deterministic scan and fix what it flags
version: 1.0
trust: built-in
---

When you've written or changed React/JSX (components, hooks, state, effects), run
[react-doctor](https://github.com/millionco/react-doctor) — a deterministic linter
that catches the mistakes agents commonly make in React — before calling the work done.

1. **Run it** (no install needed — it's an `npx` CLI; requires Node):
   `npx react-doctor@latest`
   On a large repo, scope to the files you touched.
2. **Read the report.** It flags issues across: state & effects (stale closures,
   missing/incorrect effect deps, unnecessary effects), performance (avoidable
   re-renders, missing memoization, unstable props/children), architecture,
   security, and accessibility.
3. **Fix what it flags**, judging each in context. react-doctor is deterministic
   (rule-based), so treat findings as real — but verify each fix preserves intent.
   Address the cause; don't blindly silence a rule.
4. **Re-run to confirm** the issues you touched are gone and you didn't add new ones.
5. If the project has a `doctor.config.{ts,js,json}`, respect it — it tunes which
   rules run.

This is the "your agent writes bad React → this catches it" check: cheap, fast, and
it catches the bug classes that slip past a quick read. (react-doctor also ships an
`install` command that drops editor skills/rules, and an experimental LSP server —
but for an agent loop, running the CLI on your diff is the direct path.)
