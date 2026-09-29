# 06: Unsent claims survive a restart

**What to build:** A field operator records claims through an offline period, the device process is killed, and the device restarts. Nothing recorded that day is lost, and nothing recorded that day has been silently sent. The work survives the crash.

This is the difference between an offline-first device and an offline-until-restarted one.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Claims recorded while offline are persisted in an acknowledged on-disk queue, not an in-process queue
- [ ] Killing and restarting the process leaves every queued claim intact
- [ ] After restart the device still answers questions using those claims
- [ ] On reconnect the queue drains and the claims are delivered
- [ ] Replaying the queue is idempotent, keyed on the claim identifier, so a partially delivered queue does not duplicate
- [ ] A test kills and restarts the device and asserts through the question interface that the claims survived
