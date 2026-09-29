# 09: The same question answers differently after sync

**What to build:** An operator asks a question while offline and gets one answer. The device syncs, learns something it did not know, and the same question now returns a different answer. Both answers are shown side by side, and the verdict names the claim responsible for the change.

This is what makes the system's awareness visible: the same question, asked twice, with the difference shown rather than inferred.

**Blocked by:** 07

**Status:** ready-for-agent

- [ ] A claim arriving from the depot changes what the device believes about a subject
- [ ] The same question asked before and after sync yields different verdicts
- [ ] The verdict is CORRECTED and carries the earlier answer alongside the new one
- [ ] The verdict names the claim that caused the change
- [ ] The change is visible to an operator without needing to ask twice themselves
- [ ] A test asks the same question before and after a sync and asserts the verdict changed and names the cause
