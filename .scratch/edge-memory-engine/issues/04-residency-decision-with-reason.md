# 04: Residency decision, with its reason

**What to build:** Every claim the device holds is classified as staying on the device, syncing, or marked cloud-only â€” and every classification carries a human-readable reason naming each signal that fired, the value it held, and the threshold it crossed. A fleet operator can see why a claim was kept local, and can watch the cost of that policy change when they adjust it.

Recency is not a factor. A claim being newer does not make it more worth sending, and a claim being older does not make it safe.

**Blocked by:** 02

**Status:** done

- [x] Every claim receives exactly one residency classification of LOCAL, SYNC or CLOUD_ONLY
- [x] The verdict carries a mandatory reason string naming each signal that fired, its value, and the threshold crossed
- [x] Changing a policy weight changes the classification and the reason, and the reported outbound byte count changes with it
- [x] Sensitivity acts as a veto rather than a weight, so a sensitive claim stays local regardless of its urgency
- [x] The same inputs and ladder version reproduce the same decision
- [x] The policy reaches no network call to make a decision
- [x] A test asserts only on the classification, the reason, and the byte count
