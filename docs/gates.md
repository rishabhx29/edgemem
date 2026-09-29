# Substrate gate findings

Probed on: Windows 11, AMD Ryzen 3 5300U, 8 logical cores, 7.3 GB RAM.
Interpreter: CPython 3.12.14. Library: `qdrant-edge-py==0.8.0` (win_amd64, abi3).

Reproduce with `.\.venv\Scripts\python.exe scripts\probe_gates.py` and
`.\.venv\Scripts\python.exe scripts\probe_stall.py`.
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

## Summary

| Gate | Question | Verdict |
| --- | --- | --- |
| 0 | Wheel installs and imports | **PASS** |
| 1 | Release already fuses natively | **PASS — use it, do not hand-roll** |
| 2 | Blocking op stalls concurrent query | **PARTIAL — stalls above ~50k; isolate builds in a worker** |
| 3 | Keyword scores usable unindexed | **PASS** |

## Fallback recorded (environmental, not a gate failure)

No container runtime is available on this machine, so a Qdrant **server** cannot be
run locally. The vendor's server→edge partial-snapshot path (`snapshot_manifest` →
`/snapshot/partial/create` → `update_from_snapshot`) cannot be exercised here.

**Decision:** the engine implements its own incremental delta exchange against the
depot endpoint and reports which sync path is active. Upgrading to the vendor
snapshot path is a configuration change, not a rewrite. Surfaced in the product
rather than hidden.
