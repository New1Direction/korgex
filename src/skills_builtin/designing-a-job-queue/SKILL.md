---
name: designing-a-job-queue
description: Move slow/async work off the request path with a reliable worker queue
version: 1.0
trust: built-in
---

When work is slow, scheduled, or should survive a restart, push it to a queue and
process it with workers. The hard part isn't enqueuing — it's reliability.

1. **Make jobs idempotent.** A job WILL run more than once (retries, redelivery,
   crashes). Design so running it twice is safe — key on a stable id, check
   "already done" before acting.
2. **At-least-once, not exactly-once.** Assume duplicates and out-of-order delivery;
   exactly-once is largely a myth. Idempotency (point 1) is how you cope.
3. **Acknowledge after success.** Mark a job done only once the work committed. If a
   worker dies mid-job, the un-acked job should redeliver, not vanish.
4. **Bound retries + dead-letter.** Retry with backoff up to a limit; send
   permanent failures to a dead-letter queue for inspection — never retry forever.
5. **Visibility/lease timeouts.** A claimed job must reappear if the worker stalls;
   tune the lease so it doesn't double-run a still-healthy job.
6. **Backpressure + concurrency limits.** Cap workers so the queue can't overwhelm
   downstream (the DB, an API). Monitor depth and age — a growing backlog is a signal.
7. **Observe it** (see observability-and-logging): job counts, failures, latency,
   queue depth. A silent queue is a queue you'll discover broken too late.
