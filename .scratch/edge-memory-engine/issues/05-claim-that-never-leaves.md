# 05: A claim that never leaves the device

**What to build:** The moment a claim cannot leave the device, the cloud is left holding a position it cannot fully see â€” and the engine makes that absence legible rather than silent.

A field operator asks a question the cloud could answer. The device returns a verdict of UNRESOLVED, states exactly which claim it would need, what that claim says, and how many bytes sending it would cost. The claim stays on the device. The operator knows precisely what is missing and what it would take to get it.

This is the engine's residency policy and its honesty about the consequences of that policy, meeting.

**Blocked by:** 04

**Status:** done

- [x] A sensitive claim is held with residency LOCAL despite high urgency, and the reason names the sensitivity veto
- [x] The claim is provably absent from the depot after a sync
- [x] A question that would be answered by that claim returns a verdict of UNRESOLVED_CLOUD_REQUIRED
- [x] The verdict names the specific claim that would resolve it
- [x] The verdict states the content and the byte cost of what would need to be sent
- [x] A test asserts the claim is absent from the depot and the verdict names it
