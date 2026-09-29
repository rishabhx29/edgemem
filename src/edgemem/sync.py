"""Synchronisation, both directions, over a real socket.

A device that has been away returns with two problems, and neither is solved by
copying everything. It has work the depot has never seen, and the depot has work
the device has never seen. So a reconnect exchanges only the difference, in both
directions, and both ends can say how many bytes that took.

**Upstream** is the device's outbox drained under the residency policy. Claims
the policy sealed on the device are counted as withheld and stay where they are:
they never left, and the depot's ignorance of them is a fact to be retained, not
a queue entry to be reclaimed.

**Downstream** is a pull driven by a monotonic per-collection sequence number the
device retains. The depot numbers every claim it accepts; the device remembers
the highest sequence it has absorbed and asks for everything after it. That is the
whole difference between an incremental sync and a full copy, and it survives a
kill because the retained position is written durably after each page is absorbed
rather than once at the end.

A push does not advance that position past the device's own claims, because
another device's claim may have been numbered in between, and stepping over it
would skip that claim forever. The device therefore keeps the watermark *and* the
out-of-order sequence numbers it already holds, folding them together as the gaps
close. It is a small amount of bookkeeping and it is what makes a shared depot
safe.

**The active path is reported, never assumed.** The vendor's server-to-edge delta
is a partial snapshot driven by the shard manifest, which needs a running Qdrant
server. Where there is no server, this module runs its own delta exchange against
the depot endpoint and says so in every report. Asking for the vendor path
without a server raises rather than quietly degrading, because a report naming a
path that did not run is precisely the dishonesty this engine exists to remove.

**Nothing is deleted to make room.** A pull upserts claims the device does not
hold. A claim the device already holds is kept as it stands, and an incoming copy
under the same identity is reported rather than substituted — a claim is an
attributed assertion, and two devices asserting the same thing differently is a
disagreement, not a version to be reconciled.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable

from edgemem.domain import Claim, iso, parse_iso, utcnow
from edgemem.memory import EdgeMemory
from edgemem.outbox import Outbox, durable_read_json, durable_write_json
from edgemem.residency import Residency, ResidencyContext

SYNC_ROUTE = "/sync"
STATUS_ROUTE = "/status"

DEFAULT_DELTA_PAGE = 128
"""Claims per pull request. Paged rather than unbounded so an interrupted sync
has a position to resume from instead of restarting the whole transfer."""

DEFAULT_TIMEOUT_SECONDS = 15.0

LAST_SYNC_SUFFIX = ".last-sync"

VENDOR_UNAVAILABLE = (
    "the vendor shard-manifest path needs a running Qdrant server to serve a "
    "partial snapshot; {fallback} is the path that runs without one, and the "
    "active path is reported in every sync"
)


class SyncPath(str, Enum):
    """Which mechanism is actually moving claims across the link."""

    DEPOT_DELTA = "depot_delta"
    """Incremental delta exchange against the depot endpoint, driven by a
    monotonic per-collection sequence. The path that runs without a server."""

    VENDOR_SNAPSHOT = "vendor_snapshot"
    """The vendor's shard-manifest partial snapshot. Declared, and refused
    without a running Qdrant server rather than simulated."""


class ProtocolError(RuntimeError):
    """The peer said something that cannot be reconciled with what it holds."""


class VendorSnapshotUnavailable(RuntimeError):
    """The vendor path was asked for and cannot run here.

    Raised rather than silently falling back. The fallback exists and is the
    right default; choosing it on the operator's behalf while reporting the
    vendor path is not.
    """


def _require_runnable(path: SyncPath) -> SyncPath:
    resolved = SyncPath(path)
    if resolved is SyncPath.VENDOR_SNAPSHOT:
        raise VendorSnapshotUnavailable(
            VENDOR_UNAVAILABLE.format(fallback=SyncPath.DEPOT_DELTA.value)
        )
    return resolved


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncReport:
    """What a reconnect actually cost, in both directions.

    Every byte figure is measured off bytes that crossed the socket, at the end
    that moved them. The device measures what it sent and what it received; the
    depot measures what it received and how many bytes of claims it put on the
    wire. Where two ends measured the same transfer, both figures are carried
    rather than one being presented as if it were the only measurement.
    """

    path: str
    """The mechanism that moved the claims. A statement of what ran."""

    device_id: str
    depot_id: str
    depot_path: str
    """What the depot reports it is running. Differs from ``path`` when the two
    ends disagree, which is worth surfacing rather than resolving locally."""

    started_at: str
    duration_ms: float
    offline_seconds: float
    """Measured from the last sync this device completed. Zero on the first one,
    because there is no honest figure for a gap that has no measured end."""

    pushed: int
    accepted: int
    duplicates: int
    """Envelopes the depot recognised by claim id. A non-zero count here is the
    visible evidence that a replay was idempotent rather than duplicating."""

    withheld: int
    """Claims the residency policy refused to transmit. They remain on the
    device, and remain absent from the depot."""

    pulled: int
    bytes_sent: int
    bytes_received: int
    depot_bytes_received: int
    depot_delta_bytes: int
    """Measured at the depot: the serialised size of the claims it sent down to
    this device during this sync."""

    cursor_before: int
    cursor_after: int
    held_during_apply: int
    """Local claims written while this sync was in flight. Each waited behind the
    write gate and was applied, never overwritten by the page it landed beside."""

    reapplied: int
    """Local claims kept over an incoming copy carrying the same identity."""

    complete: bool
    """False when the exchange stopped with claims still owing in either
    direction, which is a resumable position rather than a failure."""

    notes: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "device_id": self.device_id,
            "depot_id": self.depot_id,
            "depot_path": self.depot_path,
            "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 4),
            "offline_seconds": round(self.offline_seconds, 3),
            "pushed": self.pushed,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "withheld": self.withheld,
            "pulled": self.pulled,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "depot_bytes_received": self.depot_bytes_received,
            "depot_delta_bytes": self.depot_delta_bytes,
            "cursor_before": self.cursor_before,
            "cursor_after": self.cursor_after,
            "held_during_apply": self.held_during_apply,
            "reapplied": self.reapplied,
            "complete": self.complete,
            "notes": list(self.notes),
        }

    def describe(self) -> str:
        return (
            f"{self.path}: {self.pushed} up ({self.accepted} new, "
            f"{self.duplicates} already held), {self.pulled} down, "
            f"{self.bytes_sent + self.bytes_received} bytes over the socket, "
            f"cursor {self.cursor_before}->{self.cursor_after}"
        )


# --------------------------------------------------------------------------
# the wire
# --------------------------------------------------------------------------


def envelope(claim: Claim) -> dict[str, Any]:
    """One claim as it travels, with its identity stated outside the payload.

    The claim id is the replay key at both ends, so it is carried in the clear
    rather than something the receiver has to dig out of the payload to find.
    """
    return {"claim_id": claim.claim_id, "payload": claim.to_payload()}


def claim_from_envelope(wire: dict[str, Any]) -> Claim:
    """Rebuild a claim, insisting the envelope and its payload agree.

    A payload naming a different claim than the envelope does is a protocol error
    rather than something to resolve in favour of one of the two: resolving it
    would mean either storing under an identity nobody sent, or dropping an
    assertion whose key could not be vouched for.
    """
    if not isinstance(wire, dict):
        raise ProtocolError(f"envelope is not an object: {wire!r}")
    claim_id = wire.get("claim_id")
    payload = wire.get("payload")
    if not isinstance(claim_id, str) or not claim_id:
        raise ProtocolError(f"envelope carries no claim id: {wire!r}")
    if not isinstance(payload, dict):
        raise ProtocolError(f"envelope {claim_id} carries no payload")
    carried = payload.get("claim_id")
    if carried is not None and carried != claim_id:
        raise ProtocolError(
            f"envelope id {claim_id!r} disagrees with payload id {carried!r}"
        )
    return Claim.from_payload(claim_id, payload)


def _serialised(wire: dict[str, Any]) -> int:
    return len(
        json.dumps(wire, separators=(",", ":"), default=str).encode("utf-8")
    )


# --------------------------------------------------------------------------
# the depot's order of change
# --------------------------------------------------------------------------


class Ledger:
    """The depot's order of change: one monotonic sequence number per claim.

    Append-only and fsynced, for the same reason the device's outbox is. A depot
    that forgets what it accepted before it crashed will accept it again, and the
    device recognising its own claim is what keeps that harmless. A depot that
    renumbered, by contrast, could hand one claim two identities and leave a
    device waiting forever on a sequence that was skipped.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._order: list[str] = []
        self._seq_of: dict[str, int] = {}
        self._replay()

    def _replay(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8", newline="\n") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                claim_id = record.get("claim_id")
                seq = record.get("seq")
                if not isinstance(claim_id, str) or not isinstance(seq, int):
                    continue
                if claim_id in self._seq_of:
                    continue
                self._order.append(claim_id)
                self._seq_of[claim_id] = seq

    def append(self, claim_id: str) -> int:
        """Give a claim the next sequence number and make that durable."""
        with self._lock:
            seq = self._next_seq()
            record = {"seq": seq, "claim_id": claim_id, "at": iso(utcnow())}
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._order.append(claim_id)
            self._seq_of[claim_id] = seq
            return seq

    def _next_seq(self) -> int:
        return (max(self._seq_of.values()) + 1) if self._seq_of else 1

    def next_seq(self) -> int:
        with self._lock:
            return self._next_seq()

    def seq_of(self, claim_id: str) -> int | None:
        with self._lock:
            return self._seq_of.get(claim_id)

    def since(
        self, cursor: int, exclude: set[int], limit: int
    ) -> list[tuple[int, str]]:
        """Numbered claims after ``cursor``, oldest first.

        ``exclude`` carries sequences the caller already holds out of order.
        Returning them again would cost bytes to teach the device something it
        knows, and the fold that follows would stall waiting for a gap that is
        not coming.
        """
        if limit <= 0:
            return []
        with self._lock:
            found = [
                (self._seq_of[cid], cid)
                for cid in self._order
                if self._seq_of[cid] > cursor and self._seq_of[cid] not in exclude
            ]
        found.sort()
        return found[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._seq_of)


# --------------------------------------------------------------------------
# the depot
# --------------------------------------------------------------------------


class Depot:
    """The central side, as a real listener on a real socket.

    A second :class:`EdgeMemory` rather than a store of its own, so the depot
    answers questions through the same seam the device does and a claim that
    arrived centrally is retrievable centrally by asking about it. What the depot
    adds is the ledger that makes the downlink incremental, and the byte
    accounting that makes the transfer auditable from either end.
    """

    def __init__(
        self,
        memory: EdgeMemory,
        ledger_path: Path,
        depot_id: str = "DEPOT-1",
        sync_path: SyncPath = SyncPath.DEPOT_DELTA,
    ) -> None:
        self.memory = memory
        self.depot_id = depot_id
        self.path = _require_runnable(sync_path)
        self.ledger = Ledger(ledger_path)
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._counters = {
            "requests": 0,
            "bytes_received": 0,
            "bytes_sent": 0,
            "accepted": 0,
            "duplicates": 0,
        }
        self._errors: list[str] = []

    # -- lifecycle ---------------------------------------------------------

    def serve(self, host: str = "127.0.0.1", port: int = 0) -> str:
        """Bind and serve in a background thread. Returns the base URL."""
        if self._server is not None:
            return self.url
        server = ThreadingHTTPServer(
            (host, port), partial(_SyncHandler, depot=self)
        )
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name=f"depot-{self.depot_id}", daemon=True
        )
        self._thread.start()
        return self.url

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server, self._thread = None, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("the depot is not serving")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # -- reporting ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """What the depot is running, and what it has been sent.

        The active path is stated here as well as in every device report, so an
        operator looking at the centre and an operator looking at a device cannot
        be told different things about how claims are moving.
        """
        with self._lock:
            counters = dict(self._counters)
            errors = list(self._errors)
        return {
            "depot_id": self.depot_id,
            "path": self.path.value,
            "claims": self.memory.stats()["claims"],
            "numbered": self.ledger.count(),
            "next_seq": self.ledger.next_seq(),
            "errors": errors,
            **counters,
        }

    def meter(self, **deltas: int) -> None:
        with self._lock:
            for name, delta in deltas.items():
                self._counters[name] = self._counters.get(name, 0) + delta

    def note_error(self, message: str) -> None:
        with self._lock:
            self._errors.append(message)
            del self._errors[:-20]

    # -- the protocol ------------------------------------------------------

    def exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        """One round of the protocol, independent of the transport.

        Envelopes are absorbed and made durable before they are numbered. A crash
        between the two leaves a claim in the depot's memory but unnumbered, and
        the next push of that claim re-absorbs it idempotently and numbers it
        then. Numbering first would leave the ledger naming a claim nobody could
        read back.
        """
        if not isinstance(request, dict):
            raise ProtocolError("a sync request must be a JSON object")
        device_id = str(request.get("device_id", "")).strip()
        if not device_id:
            raise ProtocolError("a sync request must name the device it is from")
        try:
            cursor = int(request.get("cursor", 0))
            known = {int(s) for s in request.get("known") or []}
            page_size = int(request.get("max_delta", DEFAULT_DELTA_PAGE))
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"malformed sync position: {exc}") from exc

        accepted: list[str] = []
        duplicates: list[str] = []
        numbered: list[dict[str, Any]] = []

        inbound = list(request.get("envelopes") or [])
        claims = [claim_from_envelope(wire) for wire in inbound]
        unseen = [c for c in claims if self.ledger.seq_of(c.claim_id) is None]
        if unseen:
            self.memory.absorb(unseen)
            self.memory.flush()
        for claim in claims:
            seq = self.ledger.seq_of(claim.claim_id)
            if seq is None:
                seq = self.ledger.append(claim.claim_id)
                accepted.append(claim.claim_id)
            else:
                duplicates.append(claim.claim_id)
            numbered.append({"claim_id": claim.claim_id, "seq": seq})
        self.meter(accepted=len(accepted), duplicates=len(duplicates))

        # A claim this device just sent is a claim it has, so sending it straight
        # back would cost bytes to teach the device something it said a moment
        # ago. Excluding the sequences assigned here also keeps the device's
        # retained position free of gaps it did not cause.
        known.update(int(entry["seq"]) for entry in numbered)

        wanted = self.ledger.since(cursor, known, page_size)
        delta: list[dict[str, Any]] = []
        for seq, claim_id in wanted:
            held = self.memory.store.get(claim_id)
            if held is None:
                raise ProtocolError(
                    f"the ledger numbers claim {claim_id} at {seq} but the depot "
                    "does not hold it; the change order and the memory disagree"
                )
            entry = envelope(held)
            entry["seq"] = seq
            delta.append(entry)

        return {
            "depot_id": self.depot_id,
            "path": self.path.value,
            "accepted": accepted,
            "duplicates": duplicates,
            "numbered": numbered,
            "delta": delta,
            "delta_bytes": sum(_serialised(entry) for entry in delta),
            "has_more": len(self.ledger.since(cursor, known, page_size + 1))
            > len(wanted),
        }


# --------------------------------------------------------------------------
# the transport
# --------------------------------------------------------------------------


class _SyncHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args: Any, depot: Depot, **kwargs: Any) -> None:
        self.depot = depot
        super().__init__(*args, **kwargs)

    def do_POST(self) -> None:  # noqa: N802 - the stdlib names it this
        if self.path != SYNC_ROUTE:
            self._reply(404, b'{"error":"no such route"}')
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        self.depot.meter(requests=1, bytes_received=len(raw))
        try:
            reply = self.depot.exchange(json.loads(raw))
            reply["bytes_received"] = len(raw)
            body = json.dumps(reply, separators=(",", ":"), default=str).encode("utf-8")
        except ProtocolError as exc:
            self.depot.note_error(f"protocol error: {exc}")
            self._reply(400, json.dumps({"error": str(exc)}).encode("utf-8"))
            return
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the depot
            self.depot.note_error(f"{type(exc).__name__}: {exc}")
            self._reply(
                500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode("utf-8")
            )
            return
        self._reply(200, body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != STATUS_ROUTE:
            self._reply(404, b'{"error":"no such route"}')
            return
        self._reply(
            200, json.dumps(self.depot.status(), default=str).encode("utf-8")
        )

    def _reply(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.depot.meter(bytes_sent=len(body))

    def log_message(self, fmt: str, *args: Any) -> None:
        """Stay quiet on stderr. A refused request is recorded on the depot and
        returned in the response body, so there is nothing here worth printing."""

    def log_error(self, fmt: str, *args: Any) -> None:
        self.log_message(fmt, *args)


# --------------------------------------------------------------------------
# the device side
# --------------------------------------------------------------------------


@dataclass
class PullCursor:
    """Where the device is in the depot's order of change.

    A contiguous watermark plus the sequence numbers this device already holds
    above it. Both halves are needed: a shared depot numbers claims from every
    device, so this device's own claims routinely come back numbered past a claim
    it has not pulled yet, and a bare watermark would step over that claim and
    never deliver it.

    Written durably after each page is absorbed, which is what makes an
    interrupted sync resumable: the next attempt asks from the last page that
    actually landed rather than from the beginning.
    """

    path: Path
    cursor: int = 0
    known: set[int] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "PullCursor":
        stored = durable_read_json(path, {"cursor": 0, "known": []})
        raw_known = stored.get("known")
        return cls(
            path=Path(path),
            cursor=int(stored.get("cursor", 0) or 0),
            known={int(s) for s in raw_known} if isinstance(raw_known, list) else set(),
        )

    def learn(self, seqs: Iterable[int]) -> int:
        """Record sequences the device now holds, and close any gap they fill."""
        self.known.update(int(s) for s in seqs)
        while self.cursor + 1 in self.known:
            self.cursor += 1
            self.known.discard(self.cursor)
        self.save()
        return self.cursor

    def save(self) -> None:
        durable_write_json(
            self.path, {"cursor": self.cursor, "known": sorted(self.known)}
        )

    def to_request(self) -> dict[str, Any]:
        return {"cursor": self.cursor, "known": sorted(self.known)}


class DeviceLink:
    """The device's half of the link: drain, exchange, absorb, report."""

    def __init__(
        self,
        memory: EdgeMemory,
        outbox: Outbox,
        depot_url: str,
        cursor_path: Path,
        depot_id: str = "DEPOT-1",
        path: SyncPath = SyncPath.DEPOT_DELTA,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.memory = memory
        self.outbox = outbox
        self.depot_url = depot_url.rstrip("/")
        self.depot_id = depot_id
        self.path = _require_runnable(path)
        self.timeout = timeout
        self.cursor = PullCursor.load(cursor_path)
        self._last_sync_path = Path(str(cursor_path) + LAST_SYNC_SUFFIX)

    def sync(
        self,
        page_size: int = DEFAULT_DELTA_PAGE,
        on_page: Callable[[int], None] | None = None,
    ) -> SyncReport:
        """Exchange what changed, in both directions, and say what it cost.

        ``on_page`` is called with the number of claims absorbed after each pull
        page, outside the write gate, so a caller can put work between pages — a
        progress display, or a local record that has to be interleaved for the
        guarantee to be worth anything. Nothing about the outcome depends on it
        being called.
        """
        started = time.perf_counter()
        started_at = utcnow()
        cursor_before = self.cursor.cursor
        writes_before = self.memory.local_writes()

        batch, withheld = self._drain()
        notes: list[str] = []
        pushed = accepted = duplicates = pulled = reapplied = 0
        bytes_sent = bytes_received = depot_rx = depot_delta_bytes = 0
        depot_path = "unreported"
        reply: dict[str, Any] = {}
        complete = False

        outstanding = [envelope(claim) for claim in batch]
        while True:
            body = json.dumps(
                {
                    "device_id": self.memory.device_id,
                    "depot_id": self.depot_id,
                    "path": self.path.value,
                    **self.cursor.to_request(),
                    "envelopes": outstanding,
                    "max_delta": max(1, int(page_size)),
                },
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            raw, reply = self._post(body)
            bytes_sent += len(body)
            bytes_received += len(raw)
            depot_rx += int(reply.get("bytes_received", 0))
            depot_delta_bytes += int(reply.get("delta_bytes", 0))
            depot_path = str(reply.get("path", depot_path))

            if outstanding:
                pushed = len(outstanding)
                accepted = len(reply.get("accepted") or [])
                duplicates = len(reply.get("duplicates") or [])
                settled = {
                    entry.get("claim_id")
                    for entry in reply.get("numbered") or []
                    if entry.get("claim_id") in set(reply.get("accepted") or [])
                    | set(reply.get("duplicates") or [])
                }
                self.outbox.ack(settled)
                self.cursor.learn(
                    int(entry["seq"])
                    for entry in reply.get("numbered") or []
                    if entry.get("claim_id") in settled
                )
                outstanding = []

            page = [claim_from_envelope(wire) for wire in reply.get("delta") or []]
            if page:
                reapplied += self._absorb(page, notes)
                pulled += len(page)
            self.cursor.learn(int(wire["seq"]) for wire in reply.get("delta") or [])
            if on_page is not None:
                on_page(len(page))
            if not reply.get("has_more"):
                complete = True
                break

        if self.path.value != depot_path:
            notes.append(
                f"device ran {self.path.value} but the depot reports {depot_path}"
            )
        owed = self.outbox.count()
        if owed:
            notes.append(
                f"{owed} claim(s) are held on the device and are not yet at the depot"
            )
        if complete:
            durable_write_json(
                self._last_sync_path,
                {
                    "at": iso(utcnow()),
                    "bytes": bytes_sent + bytes_received,
                    "path": self.path.value,
                },
            )

        return SyncReport(
            path=self.path.value,
            device_id=self.memory.device_id,
            depot_id=str(reply.get("depot_id", self.depot_id)),
            depot_path=depot_path,
            started_at=iso(started_at),
            duration_ms=(time.perf_counter() - started) * 1000,
            offline_seconds=self._offline_seconds(started_at),
            pushed=pushed,
            accepted=accepted,
            duplicates=duplicates,
            withheld=withheld,
            pulled=pulled,
            bytes_sent=bytes_sent,
            bytes_received=bytes_received,
            depot_bytes_received=depot_rx,
            depot_delta_bytes=depot_delta_bytes,
            cursor_before=cursor_before,
            cursor_after=self.cursor.cursor,
            held_during_apply=max(0, self.memory.local_writes() - writes_before),
            reapplied=reapplied,
            complete=complete,
            notes=tuple(notes),
        )

    # -- pieces ------------------------------------------------------------

    def _drain(self) -> tuple[list[Claim], int]:
        """What may leave the device, and what the policy held back.

        The residency decision is the device's own, taken per claim against what
        the device's memory already holds, and the allowance is then spent across
        the survivors. Deciding each claim in isolation would let a set of
        individually affordable claims add up to several times the budget, which
        is the one thing a byte budget is for.
        """
        candidates = self.outbox.pending()
        if not candidates:
            return [], 0
        eligible: list[Claim] = []
        withheld = 0
        for claim in candidates:
            if self.memory.residency_of(claim).residency is Residency.SYNC:
                eligible.append(claim)
            else:
                withheld += 1
        if not eligible:
            return [], withheld
        context = ResidencyContext(
            decided_at=utcnow(),
            byte_budget=self.memory.byte_budget,
            remaining_bytes=self.memory.byte_budget,
        )
        planned = self.memory.policy.plan(eligible, context)
        batch = [c for c, reason in planned if reason.residency is Residency.SYNC]
        withheld += sum(
            1 for _, reason in planned if reason.residency is not Residency.SYNC
        )
        return batch, withheld

    def _absorb(self, page: list[Claim], notes: list[str]) -> int:
        """Take a pull page into the device, holding local writes.

        The write gate is what makes "a claim written during a sync is not
        clobbered" true rather than hoped for: a local record either lands before
        the page is applied or waits for it, and cannot interleave. A claim the
        device already holds is not overwritten — the device keeps what it
        observed, and an arriving copy under the same identity is reported as a
        disagreement about identity rather than applied as an update.
        """
        incoming: list[Claim] = []
        reapplied = 0
        with self.memory.hold_writes():
            for claim in page:
                held = self.memory.store.get(claim.claim_id)
                if held is None:
                    incoming.append(claim)
                    continue
                if held.to_payload() != claim.to_payload():
                    reapplied += 1
                    notes.append(
                        f"depot re-sent claim {claim.claim_id} with different "
                        "content; the device kept its own record"
                    )
            if incoming:
                self.memory.absorb(incoming)
        return reapplied

    def _offline_seconds(self, now: Any) -> float:
        stored = durable_read_json(self._last_sync_path, {})
        try:
            return max(0.0, (now - parse_iso(str(stored["at"]))).total_seconds())
        except (KeyError, TypeError, ValueError):
            return 0.0

    def _post(self, body: bytes) -> tuple[bytes, dict[str, Any]]:
        request = urllib.request.Request(
            f"{self.depot_url}{SYNC_ROUTE}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise ProtocolError(f"depot refused the sync ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProtocolError(f"depot unreachable: {exc.reason}") from exc
        try:
            reply = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"depot reply was not JSON: {exc}") from exc
        if not isinstance(reply, dict):
            raise ProtocolError("depot reply was not an object")
        if reply.get("error"):
            raise ProtocolError(f"depot reported an error: {reply['error']}")
        return raw, reply
