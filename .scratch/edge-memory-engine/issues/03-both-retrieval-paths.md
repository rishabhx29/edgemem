# 03: Both retrieval paths, named and timed

**What to build:** A field operator's question is answered from two retrieval paths at once â€” dense similarity and keyword matching â€” and the verdict names both, so the operator can see that an exact term still matched even when they phrased it differently. Latency is measured and reported, never estimated.

Where the engine already performs fusion, that fusion is used. Hand-written fusion is written only for the case it cannot express, and only if the substrate gate in ticket 01 showed such a case exists.

**Blocked by:** 02

**Status:** done

- [x] A query runs both a dense and a keyword retrieval path against the device's own memory
- [x] The verdict names both paths and which claims came from each
- [x] A query whose wording differs from a stored claim still returns that claim
- [x] A query using an exact stored term returns that claim
- [x] Latency is measured in the product and reported, not estimated
- [x] If the engine fuses natively, no hand-written fusion is added; if it does not, fusion exists only where the engine cannot express the case
