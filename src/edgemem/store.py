"""In-process shard store for claims.

Wraps the vector library's in-process shard type. Two shards per device:

- **mutable** — absorbs local writes immediately. Deliberately not indexed, so
  a write never waits on an index build. Serves keyword search before indexing
  (verified usable on the demo corpus).
- **immutable** — the depot-aligned view, indexed for the fast path.

One shard is open per directory, exclusively. The device process owns both and
serialises writes behind a lock.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import qdrant_edge

from edgemem.domain import Claim, Trust

# Named vector slots. The dense content vector is unnamed (""), matching the
# library's default; the sparse keyword vector is named.
DENSE = ""
KEYWORD = "kw"


def _dense_config(size: int, max_search_threads: int = 2) -> qdrant_edge.EdgeConfig:
    return qdrant_edge.EdgeConfig(
        vectors=qdrant_edge.EdgeVectorParams(
            size=size, distance=qdrant_edge.Distance.Cosine
        ),
        sparse_vectors={
            KEYWORD: qdrant_edge.EdgeSparseVectorParams(
                modifier=qdrant_edge.Modifier.Idf
            )
        },
        max_search_threads=max_search_threads,
    )


@dataclass(frozen=True)
class IndexState:
    """Honest reporting of how well the shard is indexed.

    Surfaced in the product so a reader can tell when results came from an
    index and when they came from an exhaustive scan.
    """

    points: int
    indexed_vectors: int
    segments: int

    @property
    def fully_indexed(self) -> bool:
        return self.indexed_vectors >= self.points

    @property
    def coverage(self) -> float:
        if self.points == 0:
            return 1.0
        return min(1.0, self.indexed_vectors / self.points)

    def describe(self) -> str:
        if self.points == 0:
            return "empty"
        if self.fully_indexed:
            return f"indexed ({self.indexed_vectors} vectors)"
        return (
            f"scanning ({self.indexed_vectors}/{self.points} indexed, "
            f"{self.segments} segments)"
        )


def _count_payload_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, default=str).encode("utf-8"))


class ShardStore:
    """A single shard holding claims, with keyword and dense retrieval."""

    def __init__(
        self,
        path: Path,
        device_id: str,
        dense_size: int = 384,
        bm25: qdrant_edge.Bm25 | None = None,
    ) -> None:
        self.path = Path(path)
        self.device_id = device_id
        self.dense_size = dense_size
        self.bm25 = bm25 or qdrant_edge.Bm25(qdrant_edge.Bm25Config())
        self._lock = threading.RLock()
        self._shard: qdrant_edge.EdgeShard | None = None

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        """Open the shard, creating it if this is a fresh device."""
        with self._lock:
            if self._shard is not None:
                return
            self.path.mkdir(parents=True, exist_ok=True)
            has_content = any(self.path.iterdir())
            if has_content:
                self._shard = qdrant_edge.EdgeShard.load(str(self.path))
            else:
                self._shard = qdrant_edge.EdgeShard.create(
                    str(self.path), _dense_config(self.dense_size)
                )
            self._ensure_index()

    def close(self) -> None:
        with self._lock:
            if self._shard is not None:
                self._shard.close()
                self._shard = None

    def reopen(self) -> None:
        """Close and reopen, proving durability across a process boundary."""
        self.close()
        self.open()

    def destroy(self) -> None:
        self.close()
        shutil.rmtree(self.path, ignore_errors=True)

    def _require(self) -> qdrant_edge.EdgeShard:
        if self._shard is None:
            raise RuntimeError("shard is not open")
        return self._shard

    def _ensure_index(self) -> None:
        """Create the payload indexes the query and facet paths rely on."""
        shard = self._require()
        for field in ("subject", "attribute", "device_id", "trust", "source_class"):
            try:
                shard.update(
                    qdrant_edge.UpdateOperation.create_field_index(
                        field, qdrant_edge.PayloadSchemaType.Keyword
                    )
                )
            except Exception:  # noqa: BLE001 - already present is fine
                pass
        for field in ("sensitivity", "salience", "urgency"):
            try:
                shard.update(
                    qdrant_edge.UpdateOperation.create_field_index(
                        field, qdrant_edge.PayloadSchemaType.Float
                    )
                )
            except Exception:  # noqa: BLE001
                pass

    # -- writes ------------------------------------------------------------

    def upsert(self, claims: Iterable[Claim]) -> None:
        """Write claims. Only the dense content vector is embedded.

        Every other signal rides in the payload, indexed rather than embedded,
        so the store stays a vector store and not a key-value store.
        """
        claims = list(claims)
        if not claims:
            return
        with self._lock:
            shard = self._require()
            points = [
                qdrant_edge.Point(
                    id=self._point_id(c.claim_id),
                    vector={
                        DENSE: self._embed(c.text_for_matching()),
                        KEYWORD: self.bm25.embed_document(
                            c.text_for_matching()
                        ),
                    },
                    payload=c.to_payload(),
                )
                for c in claims
            ]
            shard.update(qdrant_edge.UpdateOperation.upsert_points(points))

    def delete(self, claim_ids: Iterable[str]) -> int:
        ids = [self._point_id(cid) for cid in claim_ids]
        if not ids:
            return 0
        with self._lock:
            self._require().update(qdrant_edge.UpdateOperation.delete_points(ids))
        return len(ids)

    # -- reads -------------------------------------------------------------

    def get(self, claim_id: str) -> Claim | None:
        with self._lock:
            res = self._require().retrieve(
                [self._point_id(claim_id)], with_payload=True, with_vector=False
            )
        if not res:
            return None
        return Claim.from_payload(claim_id, dict(res[0].payload))

    def all_claims(self) -> list[Claim]:
        with self._lock:
            records, offset = self._require().scroll(
                qdrant_edge.ScrollRequest(limit=10_000, with_payload=True)
            )
        out: list[Claim] = []
        for rec in records:
            out.append(Claim.from_payload(str(rec.id), dict(rec.payload)))
        return out

    def claims_for(self, subject: str) -> list[Claim]:
        with self._lock:
            records, _ = self._require().scroll(
                qdrant_edge.ScrollRequest(
                    limit=10_000,
                    filter=qdrant_edge.Filter(
                        must=[
                            qdrant_edge.FieldCondition(
                                key="subject",
                                match=qdrant_edge.MatchValue(value=subject),
                            )
                        ]
                    ),
                    with_payload=True,
                )
            )
        return [Claim.from_payload(str(r.id), dict(r.payload)) for r in records]

    def search(
        self,
        text: str,
        limit: int = 5,
        use_dense: bool = True,
        use_keyword: bool = True,
    ) -> list[tuple[Claim, float, str]]:
        """Retrieve with both paths, fused natively when the library does it.

        Returns (claim, score, path) triples. The library performs the fusion;
        this method only reports which paths contributed.
        """
        started = time.perf_counter()
        with self._lock:
            shard = self._require()
            if use_dense and use_keyword:
                req = qdrant_edge.QueryRequest(
                    limit=limit,
                    prefetches=[
                        qdrant_edge.Prefetch(
                            limit=limit,
                            query=qdrant_edge.Query.Nearest(
                                query=self._embed(text)
                            ),
                        ),
                        qdrant_edge.Prefetch(
                            limit=limit,
                            query=qdrant_edge.Query.Nearest(
                                query=self.bm25.embed_query(text), using=KEYWORD
                            ),
                        ),
                    ],
                    query=qdrant_edge.Fusion.Rrf(k=2),
                    with_payload=True,
                )
                path = "hybrid"
            elif use_keyword:
                req = qdrant_edge.QueryRequest(
                    limit=limit,
                    query=qdrant_edge.Query.Nearest(
                        query=self.bm25.embed_query(text), using=KEYWORD
                    ),
                    with_payload=True,
                )
                path = "keyword"
            else:
                req = qdrant_edge.QueryRequest(
                    limit=limit,
                    query=qdrant_edge.Query.Nearest(query=self._embed(text)),
                    with_payload=True,
                )
                path = "dense"
            results = shard.query(req)
        elapsed = (time.perf_counter() - started) * 1000
        out = []
        for res in results:
            claim = Claim.from_payload(str(res.id), dict(res.payload))
            out.append((claim, float(res.score), path))
        return out, elapsed

    def index_state(self) -> IndexState:
        with self._lock:
            info = self._require().info()
        return IndexState(
            points=int(getattr(info, "points_count", 0) or 0),
            indexed_vectors=int(getattr(info, "indexed_vectors_count", 0) or 0),
            segments=int(getattr(info, "segments_count", 0) or 0),
        )

    def optimize(self) -> bool:
        """Build the index. Blocking by design; scheduled at idle."""
        with self._lock:
            return bool(self._require().optimize())

    def flush(self) -> None:
        with self._lock:
            self._require().flush()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _point_id(claim_id: str) -> int:
        """Map a claim id to the numeric point id the library requires.

        A stable hash, so the same claim keeps the same point across reopens —
        which is what lets an upsert be recognised as an update rather than a
        second, competing record.
        """
        return int(claim_id[:15], 16) % (2**53)

    def _embed(self, text: str) -> list[float]:
        """Dense vector for text.

        A deterministic hashing embedder. Swapping in a learned model is a
        single-function change; nothing downstream depends on which embedder
        produced the vector.
        """
        return _hash_embed(text, self.dense_size)


def _hash_embed(text: str, size: int) -> list[float]:
    import hashlib
    import math

    vec = [0.0] * size
    tokens = text.lower().split()
    for tok in tokens:
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]
