# Substrate gate findings

Probed on: Windows 11, AMD Ryzen 3 5300U, 8 logical cores, 7.3 GB RAM.
Interpreter: CPython 3.12.14. Library: `qdrant-edge-py==0.8.0` (win_amd64, abi3).

Reproduce with `.\.venv\Scripts\python.exe scripts\probe_gates.py`,
`.\.venv\Scripts\python.exe scripts\probe_stall.py` and
`.\.venv\Scripts\python.exe scripts\probe_kill_durability.py`.
Raw output in `var/gates/`.

---

## Gate 0 — does the wheel install and import?

**PASS.**

`uv pip install qdrant-edge-py==0.8.0` succeeded; `import qdrant_edge` works under
3.12.14. The default `python` on this machine is 3.7, which fails — the venv pins
3.12 and the exact wheel is the build input.

API surface confirmed by introspection of the installed module. Present and used:
`EdgeShard` (16 methods — `close`, `count`, `create`, `facet`, `flush`, `info`,
`load`, `optimize`, `query`, `retrieve`, `scroll`, `search`, `snapshot_manifest`,
`unpack_snapshot`, `update`, `update_from_snapshot`), `Fusion` (`Rrf`, `Dbsf`),
`Prefetch`, `Bm25`/`Bm25Config`, `EdgeSparseVectorParams`, `Modifier.Idf`,
`EdgeConfig`, `EdgeVectorParams`, `Point`, `Query`, `QueryRequest`, `Filter`,
`FieldCondition`, `MatchValue`, `UpdateOperation`, `UpdateMode`, `Distance`.

> `query_batch` is **not** present in this release. An earlier revision of this
> document listed it from external research; it does not exist and is not used.

## Gate 1 — does this release fuse dense and sparse at query time?

**PASS — and the answer is YES. Fusion must not be hand-rolled.**

`QueryRequest` accepts `prefetches` and `Fusion` exposes `Rrf` and `Dbsf`. A real
two-leg query — dense prefetch plus BM25 sparse prefetch, fused with
`Fusion.Rrf(k=2)` — executed and returned a scored result.

Verified that the fusion is genuine rather than one leg passing through: hybrid
scores do not match either leg's own scores.

The claim that fusion must be done in application code appears in Qdrant's
published agent-skill guidance but is **not** reproduced here — that guidance was
not fetched, so the discrepancy is recorded as observed behaviour of the installed
library, not as a judgement about a document this project has not read. The
practical consequence stands: use the built-in fusion.

## Gate 2 — does a blocking shard operation stall a concurrent query?

**PARTIAL — no stall at 50k points; a real stall at 120k. Index builds must be
isolated in a worker process.**

This gate was measured twice. The first probe ran `optimize()` over 400 points,
completing in 0.11 ms. A 0.11 ms window cannot detect a stall, so that run could
not support any conclusion and its result was discarded.

The re-measurement (`probe_stall.py`, `var/gates/stall_recheck.json`) used sizes
where the call takes seconds, issuing queries from a background thread throughout:

| Configuration | Points | optimize() | Query median | Query p95 | Query worst | Stalled |
| --- | --- | --- | --- | --- | --- | --- |
| Lock narrowed, optimize outside it | 50,000 | 13.6 s | 2.0 ms | 10.0 ms | 25.0 ms | no |
| Lock narrowed, optimize outside it | 120,000 | 24.7 s | 2.2 ms | 26.3 ms | **24,751 ms** | **yes** |
| Control: lock held across optimize | 50,000 | 11.1 s | 2.1 ms | — | 28.1 ms | no |

Findings:

1. **At 120,000 points one query blocked for the entire index build** (24.7 s).
   Queries that touched the segments being rewritten stalled; the rest were
   unaffected. The narrowing of `ShardStore`'s lock removed one source of
   contention but not this one.
2. **The control shows the store's own lock was not the dominant cause** at 50k —
   holding the lock across `optimize()` produced the same non-stalled result as
   narrowing it. An earlier revision of this document blamed the wrapper's lock
   exclusively; that was wrong.
3. `optimize()` is blocking and scales with corpus size. It is unsuitable for the
   live process at demonstration scale.

**Decision:** the index build runs in a **separate worker process**, and the
demonstration dataset stays far below the size at which an in-process build
stalls. `ShardStore.optimize()` is therefore never called on the question path.
This is the architectural consequence the first, under-powered probe failed to
establish.

## Gate 3 — are keyword scores usable before the shard is indexed?

**PASS — usable unindexed.**

BM25 sparse queries against the unindexed mutable shard returned non-degenerate
scores (`seal broken` → 3.284, `temperature` → 1.634), matching the
post-`optimize()` results at this corpus size.

The mutable shard can therefore serve keyword search immediately, without waiting
for an index build. Honest indexing-state reporting is still required, because at
scale an exhaustive scan is slower than the indexed path — but correctness does not
depend on it.

## Gate 4: does a shard write survive a hard process kill without an explicit flush?

**PASS, but only with the flush — which is why the outbox is a write-ahead log.**

`scripts\probe_kill_durability.py` records three claims in a child process, has
the child print their ids, and then terminates it with no shutdown, no `atexit`
and no flush. It then reopens the shard in a fresh process and asks the device
what it holds.

| Configuration | Claims after the kill | Verdict returned |
| --- | --- | --- |
| Upsert, no `flush()` | 0 | `UNRESOLVED_CLOUD_REQUIRED` |
| Upsert, then `flush()` | 3, all ids intact | `ANSWERED_LOCALLY` |

The shard buffers. An unflushed upsert is lost the moment the process is
terminated, which is the opposite of what a graceful close in the same test
process would have shown.

Two consequences, both architectural rather than cosmetic:

1. **`ShardStore.flush()` is not optional for a side others treat as
   authoritative.** `Depot.exchange` flushes before it numbers a claim in the
   ledger, because a ledger entry naming a claim the depot can no longer read is a
   sequence other devices would wait on forever.
2. **The device's outbox is a write-ahead log, not only a delivery queue.**
   `EdgeMemory.record` appends and fsyncs the queue entry *before* the shard write,
   and replays anything the queue holds and memory does not when the device is
   opened. A 45 ms flush per recorded claim was measured and rejected; the
   write-ahead entry costs nothing per claim and closes the same window.

## Summary

| Gate | Question | Verdict |
| --- | --- | --- |
| 0 | Wheel installs and imports | **PASS** |
| 1 | Release already fuses natively | **PASS — use it, do not hand-roll** |
| 2 | Blocking op stalls concurrent query | **PARTIAL — stalls above ~50k; isolate builds in a worker** |
| 3 | Keyword scores usable unindexed | **PASS** |
| 4 | Shard write survives a hard kill | **PASS with `flush()` — the outbox covers the gap** |

## Fallback recorded (environmental, not a gate failure)

A Qdrant server is now reachable — see Gate 5 — so this fallback is no longer
forced by the environment. It is chosen on evidence.

## Gate 5 — is the vendor's snapshot path usable in 0.8.0?

**NO, for the hybrid path. Full snapshots restore dense vectors; sparse vectors
do not survive, and a restored shard cannot produce a manifest.**

A Qdrant 1.19.1 server runs in WSL2 and is reachable from Windows on port 6333.
Reproduce with `.\.venv\Scripts\python.exe scripts\probe_vendor_path.py`; raw
output in `var/gates/vendor_path.json`.

| Step | Result |
| --- | --- |
| Server reachable | yes — qdrant 1.19.1 |
| Create + seed an Edge Shard | yes, 4 points |
| Create a matching server collection | yes, 4 points |
| Server-side full snapshot | 225,792 bytes |
| `unpack_snapshot` into an Edge Shard | 4 points restored |
| **Dense query against the restored shard** | **3 hits — works** |
| **BM25 sparse query against the restored shard** | **0 hits, before and after `optimize()`** |
| `snapshot_manifest()` on a restored shard | raises `Shard is not initialized` |
| Partial snapshot via manifest | unreachable — the manifest step fails |
| Reload a live shard directory | ok |

The restored shard reports `payload_schema={}` and `indexed_vectors_count=0`, which
is consistent with the sparse vectors not being carried across the snapshot
boundary. A direct follow-up probe confirmed the split: dense nearest-neighbour
returns hits from a restored shard while the sparse leg returns none, and
`optimize()` does not repair it.

**Consequence.** The vendor's server→edge mechanism is built around
`snapshot_manifest()` → `/snapshot/partial/create` → `update_from_snapshot()`, and
the first of those steps fails on a restored shard in this release. Partial
snapshots are therefore **not available** to this project, and the incremental
exchange against the depot endpoint is the working path — not a shortcut taken
because a container runtime was missing.

**What the vendor path would have given up, and did not.** Full-snapshot restore
of a dense shard works, so a device *can* be seeded from the server. The engine
uses its own delta exchange for increments and the server for central aggregation
and cross-device visibility.

**Honest framing for the demonstration.** The engine reports which path ran on
every sync, and the report names the reason: the vendor's partial-snapshot
mechanism depends on a manifest this release cannot produce for a restored shard.
That is a finding about the library, measured and reproducible, not a claim about
this engine.

## Reporting the fallback rather than claiming the vendor path

`SyncReport.path` and `Depot.status()["path"]` carry the mechanism that actually
executed, and every report on this machine says `depot_delta`. Constructing a
`Depot` or a `DeviceLink` with `SyncPath.VENDOR_SNAPSHOT` raises
`VendorSnapshotUnavailable` naming the missing server instead of quietly running
the fallback under the vendor's name. The path is a statement of what ran, not a
description of intent — which is the same rule the index-state report follows.
