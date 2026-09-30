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

import hashlib
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

# The two retrieval paths, named for the report rather than for the slot. A slot
# name is the substrate's vocabulary; these are the engine's, and they are what a
# verdict shows an operator.
DENSE_PATH = "dense"
KEYWORD_PATH = "keyword"


class PointIdCollision(RuntimeError):
    """Two distinct claim ids resolved to one storage point.

    Raised rather than absorbed. Destroying one claim to store another is the
    failure mode this engine exists to prevent.
    """


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
            # Report the number of claims, not the indexed-vector count: the
            # latter can exceed the former, and overstating it would be the one
            # dishonest number this surface exists to avoid.
            return f"indexed ({self.points} claims)"
        return (
            f"scanning ({self.indexed_vectors}/{self.points} indexed, "
            f"{self.segments} segments)"
        )


@dataclass(frozen=True)
class RetrievalHit:
    """One retrieved claim, and the retrieval paths it came back from.

    ``legs`` is measured, not inferred from the score: each leg is run on its own
    and the claim is attributed to the ones that returned it. The fused score
    carries no record of which prefetch produced a point, so an attribution
    derived from it would be a guess wearing a number.
    """

    claim: Claim
    score: float
    path: str
    """The mechanism that retrieved it: ``hybrid``, ``dense`` or ``keyword``."""

    legs: tuple[str, ...] = ()
    """The paths that returned this claim, in the order they were run."""


@dataclass(frozen=True)
class Retrieval:
    """What one retrieval ran, what it found, and what it cost.

    ``paths`` names the legs the request issued, which is a fact about the query
    rather than about its results: a query that found nothing still ran both
    paths, and a report that said otherwise would be describing the hits rather
    than the search.
    """

    hits: tuple[RetrievalHit, ...]
    elapsed_ms: float
    paths: tuple[str, ...]


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

        Refuses to overwrite a point whose payload names a different claim. The
        point id is a digest, so a collision is improbable — but an upsert that
        silently destroyed an unrelated claim would be the exact failure this
        engine exists to prevent, so it raises instead.
        """
        claims = list(claims)
        if not claims:
            return
        with self._lock:
            shard = self._require()
            self._assert_no_foreign_claims(claims)
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

    def _assert_no_foreign_claims(self, claims: list[Claim]) -> None:
        """Raise if any target point already holds a different claim."""
        shard = self._require()
        ids = [self._point_id(c.claim_id) for c in claims]
        existing = shard.retrieve(ids, with_payload=True, with_vector=False)
        incoming = {self._point_id(c.claim_id): c.claim_id for c in claims}
        for rec in existing:
            occupant = dict(rec.payload).get("claim_id")
            expected = incoming.get(int(rec.id))
            if occupant is not None and occupant != expected:
                raise PointIdCollision(
                    f"point {rec.id} holds claim {occupant!r}; refusing to "
                    f"overwrite it with claim {expected!r}"
                )

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

    def all_claims(self, page: int = 2000) -> list[Claim]:
        """Every claim on the shard.

        Pages on the scroll cursor. A fixed limit would silently drop claims
        past it, which is the one thing this engine must never do.
        """
        out: list[Claim] = []
        with self._lock:
            shard = self._require()
        offset = None
        while True:
            records, offset = shard.scroll(
                qdrant_edge.ScrollRequest(
                    limit=page, offset=offset, with_payload=True
                )
            )
            out.extend(
                Claim.from_payload(str(r.id), dict(r.payload)) for r in records
            )
            if offset is None or not records:
                break
        return out

    def claims_for(self, subject: str, page: int = 2000) -> list[Claim]:
        out: list[Claim] = []
        with self._lock:
            shard = self._require()
        offset = None
        while True:
            records, offset = shard.scroll(
                qdrant_edge.ScrollRequest(
                    limit=page,
                    offset=offset,
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
            out.extend(
                Claim.from_payload(str(r.id), dict(r.payload)) for r in records
            )
            if offset is None or not records:
                break
        return out

    def search(
        self,
        text: str,
        limit: int = 5,
        use_dense: bool = True,
        use_keyword: bool = True,
    ) -> tuple[list[tuple[Claim, float, str]], float]:
        """Retrieve with both paths, fused natively.

        Returns ``((claim, score, path), ...)`` together with the elapsed
        milliseconds, where ``path`` is the mechanism that retrieved the claim.
        The library performs the fusion; nothing here combines a score.

        A thin projection of :meth:`traced_search`, and it pays for the same
        attribution pass that one does — see that method for what the extra
        requests are and are not for.
        """
        found = self.traced_search(
            text, limit=limit, use_dense=use_dense, use_keyword=use_keyword
        )
        return [(h.claim, h.score, h.path) for h in found.hits], found.elapsed_ms

    def traced_search(
        self,
        text: str,
        limit: int = 5,
        use_dense: bool = True,
        use_keyword: bool = True,
    ) -> Retrieval:
        """Retrieve, fused by the library, and say which path returned each claim.

        **The ranking and the scores come from the fused request and from nowhere
        else.** One request is issued, carrying both prefetches and
        ``Fusion.Rrf``, and its rows are what is returned, unchanged. No score is
        combined, weighted or reordered in Python.

        **The two single-leg requests that follow are attribution only.** A fused
        score does not record which prefetch produced a point, so "which claims
        came from each path" is answered by running each leg on its own and seeing
        which claims it returns. They are issued with ``with_payload=False``
        because only the point ids are wanted, they cannot affect what was
        returned, and the engine's fusion remains the only thing that decided the
        order.

        The legs run at the same ``limit`` as the prefetches they mirror, which
        makes the attribution complete rather than approximate: reciprocal rank
        fusion admits a point only if some prefetch returned it, so every row in
        the fused answer appears in at least one leg's answer.
        """
        started = time.perf_counter()
        with self._lock:
            shard = self._require()

        dense_query = qdrant_edge.Query.Nearest(query=self._embed(text))
        keyword_query = qdrant_edge.Query.Nearest(
            query=self.bm25.embed_query(text), using=KEYWORD
        )

        if use_dense and use_keyword:
            req = qdrant_edge.QueryRequest(
                limit=limit,
                prefetches=[
                    qdrant_edge.Prefetch(limit=limit, query=dense_query),
                    qdrant_edge.Prefetch(limit=limit, query=keyword_query),
                ],
                query=qdrant_edge.Fusion.Rrf(k=2),
                with_payload=True,
            )
            legs = ((DENSE_PATH, dense_query), (KEYWORD_PATH, keyword_query))
            path = "hybrid"
        elif use_keyword:
            req = qdrant_edge.QueryRequest(
                limit=limit, query=keyword_query, with_payload=True
            )
            legs = ((KEYWORD_PATH, keyword_query),)
            path = KEYWORD_PATH
        else:
            req = qdrant_edge.QueryRequest(
                limit=limit, query=dense_query, with_payload=True
            )
            legs = ((DENSE_PATH, dense_query),)
            path = DENSE_PATH

        results = shard.query(req)

        contributed: dict[str, set[str]] = {}
        if len(legs) > 1:
            for name, leg_query in legs:
                for rec in shard.query(
                    qdrant_edge.QueryRequest(
                        limit=limit, query=leg_query, with_payload=False
                    )
                ):
                    contributed.setdefault(str(rec.id), set()).add(name)

        hits = tuple(
            RetrievalHit(
                claim=Claim.from_payload(str(res.id), dict(res.payload)),
                score=float(res.score),
                path=path,
                legs=tuple(
                    name
                    for name, _ in legs
                    if name in contributed.get(str(res.id), ())
                ),
            )
            for res in results
        )
        return Retrieval(
            hits=hits,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            paths=tuple(name for name, _ in legs),
        )

    def index_state(self) -> IndexState:
        with self._lock:
            info = self._require().info()
        return IndexState(
            points=int(getattr(info, "points_count", 0) or 0),
            indexed_vectors=int(getattr(info, "indexed_vectors_count", 0) or 0),
            segments=int(getattr(info, "segments_count", 0) or 0),
        )

    def optimize(self) -> bool:
        """Build the index.

        Deliberately not under ``_lock``. The library releases the interpreter
        lock and is safe to read while an index build is in flight; holding our
        own lock across it would re-create the process-wide stall that the
        substrate gate was run to rule out.
        """
        return bool(self._require().optimize())

    def flush(self) -> None:
        self._require().flush()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _point_id(claim_id: str) -> int:
        """Map a claim id to the numeric point id the library requires.

        A full-width digest, not a prefix. A prefix fold is 128-to-1 for any
        two ids sharing their first fifteen hex characters, which silently
        destroys claims whose ids are structured rather than random.

        The point id is a storage key only. The claim's identity is the claim
        id in its payload, and upsert verifies the two agree before writing.
        """
        digest = hashlib.blake2b(claim_id.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") & ((1 << 63) - 1)

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
