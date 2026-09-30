"""Gate 5: is the vendor's server-to-edge snapshot path reachable?

Previously unanswerable: no container runtime was available, so a Qdrant
*server* could not be run and the partial-snapshot path could not be exercised.
A server is now running in WSL2 and reachable from Windows on 6333.

This probe answers three things:
  1. Can an Edge Shard be created and seeded?
  2. Can a server-side snapshot be created and unpacked into an Edge Shard?
  3. Does a partial snapshot (driven by the shard manifest) actually reduce the
     transfer, which is the entire point of the mechanism?

If all three hold, the engine can use the vendor path instead of its own delta
exchange, and the active path becomes a configuration choice rather than a
permanent workaround.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import qdrant_edge

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "var" / "gates"
BASE = "http://127.0.0.1:6333"
COLLECTION = "gate5"
SHARD = 0
DIM = 16


def api(method: str, path: str, body=None, raw: bool = False):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = resp.read()
    return payload if raw else json.loads(payload)


def server_up() -> tuple[bool, str]:
    try:
        info = api("GET", "/")
        return True, f"{info.get('title')} {info.get('version')}"
    except (urllib.error.URLError, OSError) as exc:
        return False, str(exc)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    results: dict = {"server": "not checked"}

    up, detail = server_up()
    results["server"] = {"reachable": up, "detail": detail}
    if not up:
        print(json.dumps(results, indent=2))
        print("\nQdrant server unreachable; vendor path cannot be exercised.")
        return 1

    # -- 1. create and seed an edge shard ---------------------------------
    shard_dir = WORK / "vendor_shard"
    shutil.rmtree(shard_dir, ignore_errors=True)
    shard_dir.mkdir(parents=True, exist_ok=True)

    config = qdrant_edge.EdgeConfig(
        vectors=qdrant_edge.EdgeVectorParams(
            size=DIM, distance=qdrant_edge.Distance.Cosine
        ),
        sparse_vectors={
            "kw": qdrant_edge.EdgeSparseVectorParams(
                modifier=qdrant_edge.Modifier.Idf
            )
        },
        max_search_threads=2,
    )
    shard = qdrant_edge.EdgeShard.create(str(shard_dir), config)

    bm25 = qdrant_edge.Bm25(qdrant_edge.Bm25Config())
    texts = [
        "the trailer seal was broken at the dock",
        "reefer temperature held at minus eighteen degrees",
        "driver log book entry for the northern route",
        "cold chain excursion recorded on the second pallet",
    ]
    points = [
        qdrant_edge.Point(
            id=i + 1,
            vector={
                "": [0.1 * (i + 1) / DIM] * DIM,
                "kw": bm25.embed_document(t),
            },
            payload={"text": t, "n": i},
        )
        for i, t in enumerate(texts)
    ]
    shard.update(qdrant_edge.UpdateOperation.upsert_points(points))
    results["edge_shard_seeded"] = {"points": len(points)}

    # -- 2. seed a matching server collection -----------------------------
    try:
        api("DELETE", f"/collections/{COLLECTION}")
    except urllib.error.HTTPError:
        pass
    api(
        "PUT",
        f"/collections/{COLLECTION}",
        {
            "vectors": {"size": DIM, "distance": "Cosine"},
            "sparse_vectors": {"kw": {}},
        },
    )
    upserted = []
    for i, t in enumerate(texts):
        upserted.append(
            {
                "id": i + 1,
                "vector": [0.1 * (i + 1) / DIM] * DIM,
                "payload": {"text": t, "n": i},
            }
        )
    api("PUT", f"/collections/{COLLECTION}/points?wait=true", {"points": upserted})
    api(
        "PUT",
        f"/collections/{COLLECTION}/index?wait=true",
        {"field_name": "text", "field_schema": "keyword"},
    )
    results["server_collection_seeded"] = {
        "collection": COLLECTION,
        "points": len(upserted),
    }

    # -- 3. full snapshot -> unpack into a fresh edge shard ---------------
    full_bytes = api(
        "GET", f"/collections/{COLLECTION}/shards/{SHARD}/snapshot", raw=True
    )
    results["full_snapshot_bytes"] = len(full_bytes)

    restored_dir = WORK / "vendor_restored"
    shutil.rmtree(restored_dir, ignore_errors=True)
    restored_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "full.snapshot"
        snap.write_bytes(full_bytes)
        qdrant_edge.EdgeShard.unpack_snapshot(str(snap), str(restored_dir))
    restored = qdrant_edge.EdgeShard.load(str(restored_dir))
    restored_info = restored.info()
    results["unpacked"] = {
        "points": int(getattr(restored_info, "points_count", 0) or 0),
    }

    # A keyword query against the restored shard must return a real hit.
    found = restored.query(
        qdrant_edge.QueryRequest(
            limit=3,
            query=qdrant_edge.Query.Nearest(
                query=bm25.embed_query("seal broken"), using="kw"
            ),
        )
    )
    results["query_against_restored"] = {
        "returned": len(found),
        "top_score": round(float(found[0].score), 6) if found else None,
    }
    restored.close()

    # -- 4. partial snapshot driven by the shard manifest ------------------
    # A shard restored from a snapshot may not be manifest-capable, so each
    # step is reported independently rather than aborting the whole probe.
    try:
        manifest = restored.snapshot_manifest()
        results["manifest"] = {"ok": True, "type": type(manifest).__name__}
        partial_bytes = api(
            "POST",
            f"/collections/{COLLECTION}/shards/{SHARD}/snapshot/partial/create",
            body=manifest,
            raw=True,
        )
        results["partial_snapshot_bytes"] = len(partial_bytes)
        results["partial_is_smaller"] = len(partial_bytes) < len(full_bytes)
        with tempfile.TemporaryDirectory() as td:
            psnap = Path(td) / "partial.snapshot"
            psnap.write_bytes(partial_bytes)
            target = WORK / "vendor_apply"
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            qdrant_edge.EdgeShard.unpack_snapshot(str(psnap), str(target))
            applied = qdrant_edge.EdgeShard.load(str(target))
            applied_info = applied.info()
            results["partial_applied"] = {
                "ok": True,
                "points": int(getattr(applied_info, "points_count", 0) or 0),
            }
            applied.close()
    except Exception as exc:  # noqa: BLE001
        results["partial_snapshot"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    # -- 5. does update_from_snapshot apply a delta to a live shard? -------
    try:
        live_dir = WORK / "vendor_live"
        shutil.rmtree(live_dir, ignore_errors=True)
        shutil.copytree(shard_dir, live_dir)
        live = qdrant_edge.EdgeShard.load(str(live_dir))
        before = int(getattr(live.info(), "points_count", 0) or 0)
        live.close()
        results["live_shard_reload"] = {"ok": True, "points_before": before}
    except Exception as exc:  # noqa: BLE001
        results["live_shard_reload"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    shard.close()
    out = WORK / "vendor_path.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
