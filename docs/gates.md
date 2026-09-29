# Substrate gate findings — ticket 01

Probed on: Windows 11, AMD Ryzen 3 5300U, 8 logical cores, 7.3 GB RAM.
Interpreter: CPython 3.12.14. Library: `qdrant-edge-py==0.8.0` (win_amd64, abi3).

Reproduce with `python scripts/probe_gates.py`. Raw output in `var/gates/gates.json`.

---

## Gate 0 — does the wheel install and import?

**PASS.**

`uv pip install qdrant-edge-py==0.8.0` succeeded; `import qdrant_edge` works under 3.12.14.
The single largest project risk is retired. The default `python` on this machine is 3.7,
which would have failed — the venv pins 3.12 and the build input is the exact wheel.

API surface confirmed present and usable: `Fusion` (`Rrf`, `Dbsf`), `Prefetch`,
`Bm25`/`Bm25Config`, `IdfParams`, `DecayKind`, `Formula`, `Mmr`, `FacetRequest`,
`UpdateOperation`, `snapshot_manifest`, `update_from_snapshot`, `query_batch`.

## Gate 1 — does this release already fuse dense and sparse at query time?

**PASS — and the answer is YES. Do not hand-roll fusion.**

`QueryRequest` accepts `prefetches`; `Fusion` exposes `Rrf` and `Dbsf`. A real two-leg
query (dense prefetch + BM25 sparse prefetch, fused with `Fusion.Rrf(k=2)`) executed and
returned a scored result.

The published `qdrant-edge` agent-skill guidance stating that fusion must be done in
application code is **stale relative to 0.8.0**. Ticket 03 is therefore written to *use*
native fusion, and to hand-roll nothing.

**Consequence for the pitch:** "we fused dense and BM25 ourselves" is not available as a
differentiator. The differentiator stays the residency decision and the conflict register.

## Gate 2 — does a blocking shard operation stall a concurrent query?

**PASS — it does not stall.**

A background thread issued 91 queries while the main thread ran `optimize()`.
Median query latency 0.2 ms, worst 0.543 ms. Queries continued throughout.

The binding **releases the GIL**. The mid-demo freeze that was the largest structural
threat to the demonstration does not occur, and the architecture does not need to move
snapshot work into a separate worker process.

Caveat: this was measured on a 400-point shard. Large shards will make `optimize()`
longer; the no-stall property should be re-measured on the demonstration dataset.

## Gate 3 — are keyword scores usable before the shard is indexed?

**PASS — usable unindexed.**

BM25 sparse queries against the unindexed mutable shard returned non-degenerate scores
(`seal broken` → 3.284, `temperature` → 1.634), identical to post-`optimize()` results at
this corpus size.

This means the mutable shard can serve keyword search immediately, without waiting for
`optimize()`. The honest-indexing-state reporting in ticket 02 is still required, because
at scale the exhaustive scan will be slower than the indexed path — but correctness does
not depend on it.

## Summary

| Gate | Question | Verdict |
| --- | --- | --- |
| 0 | Wheel installs and imports | **PASS** |
| 1 | Release already fuses natively | **PASS — yes, use it** |
| 2 | Blocking op stalls concurrent query | **PASS — does not stall** |
| 3 | Keyword scores usable unindexed | **PASS — usable** |

No fallbacks required. All four gates passed on the first substrate.

## Fallback recorded (environmental, not a gate failure)

No container runtime is available on this machine, so a Qdrant **server** cannot be run
locally. The vendor's server→edge partial-snapshot path (`snapshot_manifest` →
`/snapshot/partial/create` → `update_from_snapshot`) therefore cannot be exercised here.

**Decision:** the engine implements its own incremental delta exchange against the depot
endpoint, and reports which sync path is active. Upgrading to the vendor snapshot path is a
configuration change, not a rewrite. This is surfaced in the product rather than hidden.
