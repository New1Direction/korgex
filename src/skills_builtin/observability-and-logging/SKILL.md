---
name: observability-and-logging
description: Instrument code so failures are diagnosable in production, without noise
version: 1.0
trust: built-in
---

You can't fix what you can't see. Good observability is the difference between a
five-minute diagnosis and an all-night guess — but noisy logging is its own problem.

1. **Log events, not narration.** Record meaningful state transitions, decisions,
   and errors — not "entering function". Each log line should help answer "what
   happened and why" later.
2. **Use levels deliberately.** ERROR = needs attention; WARN = recoverable oddity;
   INFO = significant lifecycle events; DEBUG = detail for diagnosis. Don't log
   everything at INFO.
3. **Make logs structured + correlatable.** Prefer key/value (or JSON) over prose so
   they're searchable; carry an id (request/trace/session) through related lines so
   one flow can be reconstructed.
4. **Include the context to act.** On an error, log what was being attempted, the
   key inputs, and the cause — enough to reproduce. A bare "failed" is useless.
5. **NEVER log secrets or PII.** Tokens, passwords, keys, personal data must not hit
   logs. Redact at the boundary.
6. **Metrics for the steady state, logs for the incident.** Count/timing for rates
   and latency (the "is it healthy?"); detailed logs for the "why did THIS fail?".
7. Fail-safe: instrumentation must never crash or materially slow the path it
   measures.
