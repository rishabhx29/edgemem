"""Substrate gate probe.

Answers the three questions ticket 01 exists to settle:
  1. Does this release already fuse dense and sparse at query time?
  2. Does a blocking shard operation stall a concurrent query?
  3. Are keyword scores usable before the shard is indexed?

Writes its findings to gates.json and prints them. No production code here.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "var" / "gates"

import qdrant_edge  # noqa: E402


def fresh(name: str) -> Path:
    path = VENDOR / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def find(cls, name):
    return getattr(cls, name, None)


def probe_api() -> dict:
    shard_methods = sorted(
        m for m in dir(qdrant_edge.EdgeShard) if not m.startswith("_")
    )
    return {
        "qdrant_edge_version": getattr(qdrant_edge, "__version__", "unknown"),
        "edge_shard_methods": shard_methods,
        "has_query_batch": "query_batch" in shard_methods,
        "has_facets": "facet" in shard_methods,
        "has_manifest": "snapshot_manifest" in shard_methods,
        "has_update_from_snapshot": "update_from_snapshot" in shard_methods,
        "has_query_groups": "query_groups" in shard_methods,
        "has_search_matrix": "search_matrix" in shard_methods,
        "top_level_names": sorted(
            n for n in dir(qdrant_edge) if not n.startswith("_")
        ),
    }


def make_shard(path: Path, dim: int = 8):
    config = qdrant_edge.EdgeConfig(
        vectors=qdrant_edge.EdgeVectorParams(
            size=dim, distance=qdrant_edge.Distance.Cosine
        ),
        sparse_vectors={
            "kw": qdrant_edge.EdgeSparseVectorParams(
                modifier=qdrant_edge.Modifier.Idf
            )
        },
    )
    return qdrant_edge.EdgeShard.create(str(path), config)


def gate3_keyword_unindexed() -> dict:
    """Are BM25 sparse scores usable before optimize()?"""
    bm25 = qdrant_edge.Bm25(qdrant_edge.Bm25Config())
    dim = 8
    shard = make_shard(fresh("kw"), dim)
    docs = [
        "the trailer seal was broken at the dock",
        "reefer temperature held at minus eighteen degrees",
        "driver log book entry for the northern route",
    ]
    vecs = [[0.1] * dim, [0.9] + [0.1] * (dim - 1), [0.5] * dim]
    points = [
        qdrant_edge.Point(
            id=i,
            vector={"": vecs[i], "kw": bm25.embed_document(docs[i])},
            payload={"text": docs[i]},
        )
        for i in range(len(docs))
    ]
    shard.update(qdrant_edge.UpdateOperation.upsert_points(points))

    def keyword_probe(label: str) -> dict:
        out = {}
        for q in ("seal broken", "temperature"):
            try:
                res = shard.query(
                    qdrant_edge.QueryRequest(
                        limit=3,
                        query=qdrant_edge.Query.Nearest(
                            query=bm25.embed_query(q), using="kw"
                        ),
                    )
                )
                scores = [float(s.score) for s in res]
                out[q] = {
                    "returned": len(scores),
                    "scores": [round(s, 6) for s in scores],
                    "degenerate": all(s == 0 for s in scores) if scores else True,
                }
            except Exception as exc:  # noqa: BLE001
                out[q] = {"error": f"{type(exc).__name__}: {exc}"}
        return {"stage": label, "results": out}

    before = keyword_probe("unindexed")
    try:
        shard.optimize()
        optimized = True
    except Exception as exc:  # noqa: BLE001
        optimized = False
        before["optimize_error"] = f"{type(exc).__name__}: {exc}"
    after = keyword_probe("after_optimize") if optimized else None

    verdict = "unknown"
    if after and not before["results"]["seal broken"].get("degenerate", True):
        verdict = "usable_unindexed"
    elif after:
        verdict = "requires_optimize"
    return {
        "verdict": verdict,
        "before": before,
        "after": after,
    }


def gate1_native_fusion() -> dict:
    """Does QueryRequest support prefetches + a Fusion scoring query?"""
    found = {
        "QueryRequest_has_prefetches": "prefetches" in dir(qdrant_edge.QueryRequest),
        "has_fusion_class": hasattr(qdrant_edge, "Fusion"),
        "has_prefetch_class": hasattr(qdrant_edge, "Prefetch"),
    }
    if hasattr(qdrant_edge, "Fusion"):
        found["fusion_members"] = [
            m for m in dir(qdrant_edge.Fusion) if not m.startswith("_")
        ]
    if hasattr(qdrant_edge, "Prefetch"):
        found["prefetch_members"] = [
            m for m in dir(qdrant_edge.Prefetch) if not m.startswith("_")
        ]
    found["native_fusion"] = bool(
        found["QueryRequest_has_prefetches"]
        and found["has_fusion_class"]
        and found["has_prefetch_class"]
    )

    # Try an actual two-leg query.
    bm25 = qdrant_edge.Bm25(qdrant_edge.Bm25Config())
    dim = 8
    shard = make_shard(fresh("fuse"), dim)
    text = "the trailer seal was broken at the dock"
    dense = [0.3] * dim
    shard.update(
        qdrant_edge.UpdateOperation.upsert_points(
            [
                qdrant_edge.Point(
                    id=1,
                    vector={"": dense, "kw": bm25.embed_document(text)},
                    payload={"text": text},
                )
            ]
        )
    )
    try:
        res = shard.query(
            qdrant_edge.QueryRequest(
                limit=3,
                prefetches=[
                    qdrant_edge.Prefetch(
                        limit=3, query=qdrant_edge.Query.Nearest(query=dense)
                    ),
                    qdrant_edge.Prefetch(
                        limit=3,
                        query=qdrant_edge.Query.Nearest(
                            query=bm25.embed_query("seal broken"), using="kw"
                        ),
                    ),
                ],
                query=qdrant_edge.Fusion.Rrf(k=2),
            )
        )
        found["two_leg_query"] = {
            "ok": True,
            "returned": len(res),
            "scores": [round(float(s.score), 6) for s in res],
        }
    except Exception as exc:  # noqa: BLE001
        found["two_leg_query"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return found


def gate2_blocking_stalls_concurrent_query() -> dict:
    """Does a long blocking op prevent a concurrent query from completing?"""
    dim = 8
    shard = make_shard(fresh("blocking"), dim)
    points = [
        qdrant_edge.Point(id=i, vector={"": [0.01 * i] * dim}, payload={"i": i})
        for i in range(400)
    ]
    shard.update(qdrant_edge.UpdateOperation.upsert_points(points))

    observed: dict = {"query_latencies_ms": [], "main_total_ms": None}
    stop = threading.Event()

    def hammer():
        probe = [0.2] * dim
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                shard.query(
                    qdrant_edge.QueryRequest(
                        limit=5, query=qdrant_edge.Query.Nearest(query=probe)
                    )
                )
            except Exception:  # noqa: BLE001
                pass
            observed["query_latencies_ms"].append(
                (time.perf_counter() - t0) * 1000
            )
            time.sleep(0.005)

    thread = threading.Thread(target=hammer, daemon=True)
    thread.start()
    time.sleep(0.3)
    t0 = time.perf_counter()
    try:
        shard.optimize()
    except Exception as exc:  # noqa: BLE001
        observed["optimize_error"] = f"{type(exc).__name__}: {exc}"
    observed["main_total_ms"] = (time.perf_counter() - t0) * 1000
    time.sleep(0.2)
    stop.set()
    thread.join(timeout=3)

    lat = observed["query_latencies_ms"]
    if not lat:
        return {"verdict": "unknown", "observed": observed}
    lat_sorted = sorted(lat)
    worst = lat_sorted[-1]
    median = lat_sorted[len(lat_sorted) // 2]
    stalled = worst > 50.0 and worst > median * 10
    return {
        "verdict": "stalls" if stalled else "does_not_stall",
        "query_count": len(lat),
        "median_ms": round(median, 3),
        "worst_ms": round(worst, 3),
        "optimize_ms": round(observed["main_total_ms"], 3),
        "detail": observed.get("optimize_error"),
    }


def main() -> int:
    VENDOR.mkdir(parents=True, exist_ok=True)
    results = {"python": sys.version, "api": probe_api()}
    for name, fn in (
        ("gate1_native_fusion", gate1_native_fusion),
        ("gate2_blocking", gate2_blocking_stalls_concurrent_query),
        ("gate3_keyword_unindexed", gate3_keyword_unindexed),
    ):
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001
            results[name] = {"fatal": f"{type(exc).__name__}: {exc}"}
    out = VENDOR / "gates.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps(results, indent=2, default=str))
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
