---
name: writing-clearly
description: Write concise, faithful prose — comments, docs, commit messages, and updates
version: 1.0
trust: built-in
---

Clear writing is a faithful, compressed model of the truth. Cut anything that
doesn't change the reader's understanding.

**General**
1. Lead with the point. Say what changed / what to do, then the why. No preamble.
2. Be concrete and specific over vague and grand. Prefer plain words to jargon.
3. Don't overstate: state verified facts plainly; flag the uncertain as uncertain.

**Code comments**
4. Comment the WHY, not the WHAT — well-named identifiers already say what. Add a
   comment only when the reason is non-obvious. Match the file's existing density.

**Docs & messages**
5. Match length to the reader's need: a commit subject is one line; a function doc
   is its contract (purpose, params, returns, failure); a status update is a
   sentence or two — what changed, what's next.
6. Show, don't tell: a short example beats a paragraph of description.

Red flags: marketing adjectives, restating code in English, walls of text where a
list or example would do, hedging on things you've verified.
