# 17: Separate the demonstration's transport rail from the question interface

**What to build:** The specification says the inspector holds no information the seam
does not produce. The sync report is not a verdict, and a demonstration which never
shows the exchange cannot demonstrate that a synchronisation happened safely. Record
the decision, amend the one sentence, and label the deviation where it appears.

**Blocked by:** 15

**Status:** done

- [x] The rail renders `SyncReport.to_payload()` and is confined to the step where an exchange occurs
- [x] It is labelled as transport at the point of use, not in a footnote
- [x] `spec.md` is amended to distinguish the question interface from the transport rail
- [x] `docs/adr/0001-demo-transport-rail.md` records the decision and the option rejected
- [x] Widening `Verdict` to carry transport counters was considered and rejected, with the coupling it would have required

## Comments

The rejected option is the interesting one. Putting the counters in the verdict would
have kept the letter of the rule, but `DeviceLink` wraps `EdgeMemory`, so the memory
would have had to reach back around itself to see them. A clean rule that requires a
cycle is worse than a labelled exception.