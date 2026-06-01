---
name: authoring-a-skill
description: Write a new reusable skill (SKILL.md) so korgex gets better at recurring work
version: 1.0
trust: built-in
---

A skill captures a durable, repeatable procedure so it doesn't have to be
re-derived. Write one when you've solved something you'll plausibly hit again.

1. **Is it skill-worthy?** Yes if it's a GENERAL, reusable procedure. No if it's a
   one-off answer or project-specific trivia (that belongs in memory/AGENTS.md).
2. **Location & format.** Create `./.korgex/skills/<kebab-name>/SKILL.md` (project)
   or `~/.korgex/skills/<kebab-name>/SKILL.md` (global). Frontmatter:
   ```
   ---
   name: <kebab-name>
   description: <one line — used to decide when to load it>
   version: 1.0
   trust: user
   ---
   ```
3. **Write the body** as a short numbered procedure: the steps to follow, the
   order, and the checks. Keep it tight — it's injected by description and loaded
   on demand, so the body should be actionable, not an essay.
4. **Scope the name well.** The description is the only thing the agent sees when
   deciding to use it, so make it specific and trigger-able.
5. Leave `trust: user`. korgex's own learned skills are `trust: agent`; never
   hand-edit a built-in.
