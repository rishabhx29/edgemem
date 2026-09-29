# 01: Walking skeleton and substrate gates

**What to build:** A device process that holds one real in-process shard, answers a trivial question about it, and keeps answering when its uplink to the depot is cut. This is the walking skeleton every later ticket hangs off, and it simultaneously settles four substrate questions that would otherwise be discovered too late: whether the published wheel installs on this machine, whether the current release already performs dense and sparse fusion at query time, whether a blocking shard operation stalls the interface, and whether keyword scores remain usable on the unindexed mutable shard.

Each gate gets a written decision and, where it fails, a recorded fallback. The gates are decisions, not assertions — they do not belong in the test suite.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The published in-process vector library installs and imports under Python 3.12, and the exact wheel is vendored into the repository as a build input
- [ ] A device process holds one shard, records at least one claim, and reads it back
- [ ] The device answers a question about that claim while the uplink is cut
- [ ] Gate 1 recorded in writing: does the release already fuse dense and sparse at query time? Yes/no, with the observed behaviour
- [ ] Gate 2 recorded in writing: does a blocking shard operation stall a concurrent query? Yes/no, with the observed behaviour
- [ ] Gate 3 recorded in writing: are keyword scores usable before the shard is indexed? Yes/no, with the observed behaviour
- [ ] A fallback is recorded for every failed gate
- [ ] The uplink is a real socket between two processes, and cutting it is externally observable
