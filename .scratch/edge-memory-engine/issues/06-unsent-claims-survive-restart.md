# 06: Unsent claims survive a restart

**What to build:** A field operator records claims through an offline period, the device process is killed, and the device restarts. Nothing recorded that day is lost, and nothing recorded that day has been silently sent. The work survives the crash.

This is the difference between an offline-first device and an offline-until-restarted one.

**Blocked by:** 02

**Status:** done

- [x] Claims recorded while offline are persisted in an acknowledged on-disk queue, not an in-process queue
- [x] Killing and restarting the process leaves every queued claim intact
- [x] After restart the device still answers questions using those claims
- [x] On reconnect the queue drains and the claims are delivered
- [x] Replaying the queue is idempotent, keyed on the claim identifier, so a partially delivered queue does not duplicate
- [x] A test kills and restarts the device and asserts through the question interface that the claims survived
