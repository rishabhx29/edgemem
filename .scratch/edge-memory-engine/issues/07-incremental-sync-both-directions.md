# 07: Incremental sync, both directions

**What to build:** When connectivity returns, the device exchanges only what changed with the depot. The device's unsent claims go up; claims the depot has that the device has not seen come down. The transfer is small, resumable, and safe to interrupt — a dropped connection mid-sync costs nothing.

The engine records which sync path is active, so an operator can see whether it is exchanging increments against the depot endpoint or applying a vendor shard restore. That is surfaced in the product, not hidden.

**Blocked by:** 06

**Status:** ready-for-agent

- [ ] A sync transfers only claims the receiving side has not seen, rather than the whole collection
- [ ] Bytes moved are measured and reported through the verdict interface
- [ ] An interrupted sync can be resumed without losing or duplicating claims
- [ ] Claims written locally during a sync are held and re-applied, not clobbered by an incoming restore
- [ ] The active sync path is reported — depot delta exchange or vendor shard restore
- [ ] A test records a claim on one device, syncs, and asserts it is present on the other with both sides reporting the byte count
