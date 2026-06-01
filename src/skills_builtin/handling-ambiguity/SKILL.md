---
name: handling-ambiguity
description: When a request is unclear or underspecified, resolve it well instead of guessing
version: 1.0
trust: built-in
---

Most failed tasks come from confidently building the wrong thing. Calibrate
between asking and assuming — both extremes waste the user's time.

1. **Separate the unknowns.** What can you resolve yourself from the code, the
   conventions, or sensible defaults? What genuinely changes what you'd build?
2. **Resolve what you can.** For anything with an obvious or conventional answer,
   pick it, state the assumption, and proceed — don't ask about it.
3. **Ask only blocking, high-leverage questions.** If an unknown would change the
   approach and you can't resolve it, ask ONE crisp question (offer concrete
   options). Use `AskUserQuestion` for real forks; don't ask permission to do the
   obvious.
4. **Scope check.** If the request is huge or spans multiple subsystems, surface
   that and propose a decomposition before diving in.
5. When you proceed on assumptions, make them visible so the user can correct
   course early rather than after the work is done.

Red flags: asking a question you could answer by reading one file; OR building for
20 minutes on an unstated assumption that turns out wrong.
