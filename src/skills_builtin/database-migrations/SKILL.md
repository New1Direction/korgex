---
name: database-migrations
description: Change a schema safely on a live system — reversible, ordered, zero-downtime
version: 1.0
trust: built-in
---

A schema change touches persistent, shared, irreplaceable data. Slow down: a bad
migration can't be "just reverted" once data is lost.

1. **Migration as code.** Use the project's migration tool; never hand-edit
   production schema. Each migration is small, ordered, version-controlled, and has
   a tested down/rollback path where possible.
2. **Additive first (expand/contract).** To change something live:
   - **Expand:** add the new column/table (nullable / with a default), deploy code
     that writes BOTH old and new.
   - **Backfill** existing rows in batches (not one giant locking update).
   - **Migrate reads** to the new shape, verify.
   - **Contract:** only after everything uses the new shape, drop the old.
   This keeps old and new code working simultaneously → zero downtime, safe rollback.
3. **Never destructive in one step.** Don't drop/rename a column in the same release
   that stops using it — you can't roll back without data loss.
4. **Guard the data.** Back up (or snapshot) before a risky migration. Test the
   migration on a copy with realistic volume — lock duration and timeouts bite at scale.
5. **Make it idempotent + resumable** where the tooling allows, so a half-run
   migration can be safely retried.

Red flag: an irreversible `DROP`/`ALTER` shipped together with the code change, or a
backfill that locks a big table in a single statement.
