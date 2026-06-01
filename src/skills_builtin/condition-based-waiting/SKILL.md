---
name: condition-based-waiting
description: Wait for a real condition or event, never a blind fixed-duration sleep
version: 1.0
trust: built-in
---

A fixed `sleep` is a guess: too short and it's flaky, too long and it's slow. Wait
for the actual condition that tells you the thing is ready.

1. **Identify the real signal.** What concretely indicates readiness? A port
   accepting connections, a line in a log, a file existing, an HTTP 200, a process
   exiting, a status field flipping.
2. **Poll the signal with a timeout.** Loop: check the condition → if true, proceed
   → else short wait and retry, up to a sensible deadline. Fail loudly on timeout
   with a useful message, don't hang forever.
3. **Prefer event/blocking APIs** when available (wait-for-exit, a readiness probe,
   an inotify/watch) over polling.
4. **In tests, never `sleep` to fix flakiness** — synchronize on the event (a
   barrier, a future, a readiness callback). A sleep that "fixes" a flaky test just
   hides the race.
5. Make the timeout and the polled condition explicit in the code so the next
   reader knows what's being awaited and for how long.

Red flag: `sleep(5)` "to be safe". That's a race waiting to happen — wait on the condition instead.
