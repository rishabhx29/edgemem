# 16: A corrected verdict must return both answers

**What to build:** `mark_unanswered` stored an empty string where the summary the
device actually gave should have been. A `CORRECTED` verdict following an
`UNRESOLVED_CLOUD_REQUIRED` one could therefore report that it had changed without
being able to show what it had changed from. The interface promised both answers and
had only one to hand.

**Blocked by:** 09

**Status:** done

- [x] `mark_unanswered` records the summary the device gave, not a placeholder
- [x] The correction path names the cause only when exactly one claim accounts for it, and declines to attribute otherwise
- [x] The demonstration reaches a correction by asking a question the device first declined, then answering it after the exchange
- [x] A test fails if a correction arrives with no previous summary

## Comments

Found by building the interface rather than by reading the engine. The demonstration
asks the reefer question twice, once before the exchange and once after, and the
second answer arrived correctly labelled but with nothing to compare against. The
defect was in the engine, not in the page that would have rendered it, so it was
fixed in the engine. `docs/gates.md` and the evidence page both record it.