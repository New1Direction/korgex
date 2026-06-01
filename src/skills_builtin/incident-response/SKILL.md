---
name: incident-response
description: When production is broken — stop the bleeding first, diagnose second, learn third
version: 1.0
trust: built-in
---

In an incident the priority order is: restore service → understand cause →
prevent recurrence. Don't invert it by debugging a live outage while users suffer.

1. **Mitigate first.** Reduce impact NOW: roll back the recent deploy, flip the
   feature flag off, fail over, scale up, or shed load. The fastest safe path to
   "users are OK again" beats the elegant fix.
2. **Stabilize, then investigate.** Once impact is contained, you have room to find
   the real cause without the clock running.
3. **Use your signals.** Logs, metrics, traces, recent changes (a deploy/config
   change right before the incident is the prime suspect). Form one hypothesis at a
   time (see systematic-debugging).
4. **Communicate.** State what's impacted, what you're doing, and the next update
   time. Silence is worse than bad news.
5. **Fix the root cause** with a regression test, and only then re-enable / re-deploy.
6. **Blameless post-mortem.** Write up the timeline, root cause, and concrete
   action items to prevent recurrence (better alerting, a guard, a test). Focus on
   the system and process, not who.

Red flag: forward-fixing a live outage instead of rolling back; declaring it
resolved without confirming recovery in the metrics.
