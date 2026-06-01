---
name: data-pipelines
description: Build reliable, resumable data connectors and transforms (ETL)
version: 1.0
trust: built-in
---

A data pipeline moves/transforms data between systems. The failure modes are about
correctness and recovery, not just throughput.

1. **Idempotent + resumable.** Pipelines fail partway. Design so re-running produces
   the same result (upsert by key, not blind insert) and can resume from a
   checkpoint rather than restarting from zero.
2. **Validate at the edges.** Check schema/types/ranges on ingest; decide up front
   what to do with bad records — reject, quarantine, or repair — never silently drop.
3. **Idempotency keys + watermarks.** Track what's been processed (a high-water mark
   or per-record key) so incremental runs don't double-count or miss late data.
4. **Batch sensibly.** Process in chunks with bounded memory; don't load an entire
   dataset into RAM. Stream where you can.
5. **Make it observable.** Record counts in/out/rejected per stage; a pipeline that
   silently processes 0 rows is a common, costly bug. Alert on anomalies.
6. **Schema evolution.** Upstream schemas change — handle added/removed fields
   gracefully (defaults, versioning) rather than crashing the whole run.
7. **Separate extract / transform / load** so each stage is testable in isolation,
   and keep raw inputs so you can reprocess after a transform bug.

Red flag: a non-resumable pipeline that must restart from scratch on any failure,
or one with no record counts so you can't tell correct from silently-empty.
