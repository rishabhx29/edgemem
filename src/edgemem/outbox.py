"""The device's durable outbox: claims written, and not yet acknowledged.

A day of offline work is worth nothing if a process kill takes it along, and an
in-process queue takes it along by construction — the queue lives in the same
address space as the work it is holding. So the queue is a file, and every append
is fsynced before the call that caused it returns. Both guarantees come from that
one decision.

**Durability.** :meth:`Outbox.enqueue` does not return until the claim's bytes
are on the disk. A claim whose enqueue never returned is a claim nobody was told
was saved, and that is the honest boundary: the acknowledgement, not the attempt,
is the promise.

**At-least-once delivery.** An entry leaves the file only once the depot has
acknowledged its claim id. A kill anywhere before that leaves the entry where it
is and it is sent again on the next reconnect. Nothing is dropped on the way to
being delivered.

Idempotence is keyed on the claim id at both ends. A claim id is enqueued at most
once, and the depot recognises a claim it already holds, so replaying a partially
delivered queue costs bytes and changes nothing.

The file is also the device's write-ahead log, which is why an entry is not
removed when a claim is refused for transmission. A claim sealed on the device
has not left the device, and the central system's ignorance of it is a fact worth
retaining rather than a queue entry worth reclaiming. The journal is compacted
when acknowledgements have made it longer than the work still outstanding, and
never to make room.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable

from edgemem.domain import Claim

ENQUEUE = "enqueue"
ACK = "ack"

COMPACT_MIN_RECORDS = 64
"""Below this many journal records the rewrite costs more than the space it
recovers, so a device that syncs in small batches never pays for compaction."""

COMPACT_HEADROOM = 2
"""Compact once the journal holds this many records per outstanding entry."""

# --------------------------------------------------------------------------
# durability primitives
# --------------------------------------------------------------------------


def durable_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Replace a small document so a kill leaves either whole version.

    Written beside the target and fsynced before the rename, so the target is
    never observed half-written. Used for the device's retained pull position,
    which is the one piece of sync state whose corruption would cost data.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / (path.name + ".staging")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    with open(staging, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staging, path)
    fsync_dir(path.parent)


def durable_read_json(
    path: Path, default: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Read a document written by :func:`durable_write_json`.

    An unreadable document falls back to the default, which for a pull position
    means the beginning. Re-reading from the beginning duplicates nothing and
    loses nothing, since claims carry their own identity and the depot
    recognises one it already holds; guessing at a position the device cannot
    substantiate could do both.

    The second element says whether that happened. Rewinding is safe, so it is
    not an error, but a device that silently starts a long transfer from the
    beginning again has lost an operator's afternoon, and needs to be able to
    say so.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return dict(default), False
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return dict(default), True
    if isinstance(loaded, dict):
        return loaded, False
    return dict(default), True


def fsync_dir(path: Path) -> None:
    """Make a directory entry itself durable, where the platform allows it.

    Windows refuses to hand out a descriptor for a directory, so the call is a
    no-op there and is deliberately not an error: the file's own bytes are
    already fsynced, and the name is in the listing before any reader can look.
    """
    try:
        handle = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def _encode(record: dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), default=str) + "\n"


# --------------------------------------------------------------------------
# the queue
# --------------------------------------------------------------------------


class Outbox:
    """Claims this device has written and the depot has not acknowledged.

    The journal is append-only and holds one JSON record per line. A record whose
    write was interrupted is skipped on replay: its bytes were never
    acknowledged, so no caller was ever told that claim was saved. Skipping it
    rather than aborting matters, because a truncated tail is followed by
    perfectly good records from the next append.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._order: list[str] = []
        self._pending: dict[str, Claim] = {}
        self._sizes: dict[str, int] = {}
        self._written: set[str] = set()
        self._records = 0
        self._replay()

    # -- durability ---------------------------------------------------------

    def _replay(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8", newline="\n") as handle:
            lines = handle.readlines()
        self._records = len(lines)
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._absorb_record(record)

    def _absorb_record(self, record: dict[str, Any]) -> None:
        op = record.get("op")
        claim_id = record.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            return
        if op == ENQUEUE:
            self._written.add(claim_id)
            if claim_id in self._pending:
                return
            self._order.append(claim_id)
            self._pending[claim_id] = Claim.from_payload(
                claim_id, dict(record.get("payload") or {})
            )
            self._sizes[claim_id] = len(_encode(record).encode("utf-8"))
        elif op == ACK:
            self._written.add(claim_id)
            self._forget(claim_id)

    def _forget(self, claim_id: str) -> None:
        if self._pending.pop(claim_id, None) is None:
            return
        self._sizes.pop(claim_id, None)
        self._order.remove(claim_id)

    def _append(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(_encode(r) for r in records))
            handle.flush()
            os.fsync(handle.fileno())
        self._records += len(records)

    # -- the queue ----------------------------------------------------------

    def enqueue(self, claim: Claim) -> bool:
        """Record a claim as unsent. True when it was not already there.

        Keyed on the claim id, so writing the same claim twice queues it once.
        That is the first half of replay idempotence; the depot recognising a
        claim it already holds is the second.
        """
        record = {
            "op": ENQUEUE,
            "claim_id": claim.claim_id,
            "payload": claim.to_payload(),
        }
        with self._lock:
            if claim.claim_id in self._written:
                return False
            self._append([record])
            self._written.add(claim.claim_id)
            self._order.append(claim.claim_id)
            self._pending[claim.claim_id] = claim
            self._sizes[claim.claim_id] = len(_encode(record).encode("utf-8"))
            return True

    def pending(self, limit: int | None = None) -> list[Claim]:
        """Unacknowledged claims, oldest first.

        Oldest first because the queue is a record of the order work happened in,
        and a device reconnecting after a long offline period should not be at
        the mercy of an arbitrary ordering chosen here.
        """
        with self._lock:
            ids = self._order if limit is None else self._order[:limit]
            return [self._pending[cid] for cid in ids]

    def ack(self, claim_ids: Iterable[str]) -> int:
        """Forget claims the depot has acknowledged. Returns how many it took.

        The acknowledgement is itself an fsynced append. A kill between the depot
        answering and this landing costs a re-send, which the depot recognises —
        at-least-once in, exactly-once stored.
        """
        settled = [cid for cid in claim_ids if cid in self._pending]
        if not settled:
            return 0
        with self._lock:
            self._append([{"op": ACK, "claim_id": cid} for cid in settled])
            for cid in settled:
                self._forget(cid)
            self._maybe_compact()
            return len(settled)

    def holds(self, claim_id: str) -> bool:
        """Whether the device still owes the depot this claim."""
        with self._lock:
            return claim_id in self._pending

    def count(self) -> int:
        with self._lock:
            return len(self._pending)

    def bytes(self) -> int:
        """Measured on-disk cost of what is still outstanding.

        Not a model of what sending would cost — that is the residency policy's
        figure. This is what the queue is occupying the device's disk with.
        """
        with self._lock:
            return sum(self._sizes.values())

    def compact(self) -> int:
        """Rewrite the journal with only the entries still outstanding.

        Never called to make room for a claim. It drops acknowledged records and
        nothing else, so it cannot lose an unsent claim even if it is interrupted.
        """
        with self._lock:
            records: list[dict[str, Any]] = []
            sizes: dict[str, int] = {}
            for cid in self._order:
                record = {
                    "op": ENQUEUE,
                    "claim_id": cid,
                    "payload": self._pending[cid].to_payload(),
                }
                records.append(record)
                sizes[cid] = len(_encode(record).encode("utf-8"))
            staging = self.path.parent / (self.path.name + ".compacting")
            with staging.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write("".join(_encode(r) for r in records))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, self.path)
            fsync_dir(self.path.parent)
            self._records = len(records)
            self._sizes = sizes
            return len(records)

    def _maybe_compact(self) -> None:
        outstanding = len(self._pending)
        if self._records > COMPACT_MIN_RECORDS and self._records > COMPACT_HEADROOM * max(
            outstanding, 1
        ):
            self.compact()
