---
name: handling-webhooks
description: Receive inbound event callbacks securely and reliably
version: 1.0
trust: built-in
---

A webhook is an untrusted HTTP request from an external system announcing an event.
Treat the endpoint as a public, hostile-input surface.

1. **Verify authenticity.** Validate the provider's signature (HMAC over the raw
   body with the shared secret) before trusting anything. Reject unsigned/invalid
   requests. Compare signatures in constant time.
2. **Use the RAW body for verification.** Don't parse-then-reverify — signatures are
   over exact bytes; re-serialized JSON won't match.
3. **Respond fast, work async.** Acknowledge quickly (2xx) and do the real work on a
   queue (see designing-a-job-queue). Providers retry on slow/failed responses, so
   blocking work in the handler causes timeouts and duplicate deliveries.
4. **Expect duplicates + out-of-order.** Webhooks are at-least-once. Dedupe on the
   event id and make handling idempotent; don't assume events arrive in order.
5. **Validate + scope the payload.** It's untrusted input (see security-review) —
   validate shape, and never feed it unsanitized into a query/command/path.
6. **Return the right codes.** 2xx = received (provider stops retrying); 4xx = won't
   accept (bad signature/payload); 5xx = transient, please retry. Don't 200 a request
   you failed to actually process.
7. **Log received event ids** (not secrets) so you can reconcile missed/duplicate deliveries.
