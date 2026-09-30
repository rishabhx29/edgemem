# 07: Incremental sync, both directions

**What to build:** When connectivity returns, the device exchanges only what changed with the depot. The device's unsent claims go up; claims the depot has that the device has not seen come down. The transfer is small, resumable, and safe to interrupt â€” a dropped connection mid-sync costs nothing.

The engine records which sync path is active, so an operator can see whether it is exchanging increments against the depot endpoint or applying a vendor shard restore. That is surfaced in the product, not hidden.

**Blocked by:** 06

**Status:** done

- [x] A sync transfers only claims the receiving side has not seen, rather than the whole collection
- [x] Bytes moved are measured and reported through the verdict interface
- [x] An interrupted sync can be resumed without losing or duplicating claims
- [x] Claims written locally during a sync are held and re-applied, not clobbered by an incoming restore
- [x] The active sync path is reported â€” depot delta exchange or vendor shard restore
- [x] A test records a claim on one device, syncs, and asserts it is present on the other with both sides reporting the byte count
