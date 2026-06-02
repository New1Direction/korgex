---
name: managing-dependencies
description: Add, update, and audit third-party dependencies deliberately and safely
version: 1.0
trust: built-in
---

Every dependency is code you now own the risk of. Add and update them with intent.

1. **Do you need it?** For something small/standard, a few lines beat a new
   dependency (and its transitive tree, supply-chain risk, and upgrade burden).
   Prefer the standard library where it's reasonable.
2. **Vet before adding.** Check it's maintained, widely used, appropriately
   licensed, and from the real source (watch for typosquats). Prefer well-known
   packages over obscure ones for sensitive work.
3. **Pin and record.** Add it to the project's manifest with a sensible version
   constraint; commit the lockfile. Don't rely on "latest" implicitly.
4. **Updating:** read the changelog for breaking changes, update one major thing at
   a time, and run the full test suite after. Treat a major-version bump as a real
   change, not a rubber stamp.
5. **Security:** periodically check for known-vulnerable versions; bump promptly
   when an advisory hits a dependency you use.
6. **Match the project.** Use the existing tool/manifest/lockfile and conventions —
   don't introduce a second package manager or dependency style.

Red flag: adding a heavy dependency for a one-liner, or bumping a major version
without reading what changed or running tests.
