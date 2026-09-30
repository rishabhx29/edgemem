"""EVIDENCE ONLY. Not a production path, and not reachable from one.

This module exists so that a claim about a single-writer, last-write-wins,
point-id-keyed design can be *measured* rather than asserted. It runs the same
two concurrent claims this engine was asked to keep through the shape of that
strategy and reports which claims are still there afterwards. It is a model of
the strategy's shape, not of any product, and nothing in the engine imports it.

**What the comparison shows, stated as narrowly as the evidence allows.** A
single-writer, last-write-wins, point-id-keyed strategy has no merge semantics.
The storage key of a logical record is one point, so two concurrent writes to
that record are two writes to one key, and the one that lands second replaces
the one that landed first. The defensible claim is therefore about merge
semantics and nothing more: of two concurrent writes to the same logical record,
one is overwritten and is unrecoverable from that store. There is no retained
second version, and no field in which both could have been kept.

**What it deliberately does not claim.** It is not a claim that the vendor's
documented pattern loses data, drops unsynced writes, or destroys anything on a
clean shutdown. That vendor's reference implementation flushes before it sets
its watermark, so a device that has synced keeps what it synced, and a device
that has not synced is not overwritten by a store that has never seen its work.
None of that is in dispute here. What the shape cannot do is hold two
concurrent versions of one logical record at the same time, and that is the only
difference this module measures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from edgemem.domain import Claim

__all__ = [
    "NaiveOutcome",
    "logical_record_key",
    "naive_lww_survivors",
]
"""Every public name here is named for the strategy it models, so that a reader
who meets one in a traceback knows immediately that they are looking at
evidence and not at a code path the engine can take."""

FIELD = "\x1f"
"""Separator for the composite key.

A control character rather than a delimiter a subject or attribute could itself
contain, because a key that two different logical records can collide on is the
same class of defect this whole exercise is about."""


def logical_record_key(claim: Claim) -> str:
    """The storage key a single-writer, point-id-keyed strategy would use.

    Subject and attribute, because that is what names the record two devices
    disagree about. The value is deliberately *not* part of the key: keying on
    it would give each side of a disagreement its own row, which would store
    both writes and report agreement where there is none. A strategy that keys
    this way is not a strategy with merge semantics; it is two strategies that
    cannot see each other, and it is excluded here precisely so the comparison
    is about the keying the strategy in question actually uses.
    """
    return f"{claim.subject}{FIELD}{claim.attribute}"


@dataclass(frozen=True)
class NaiveOutcome:
    """What is left after a set of writes went through the modelled strategy.

    Plain data about the strategy, not a verdict about it. The engine's own
    ``Verdict`` is a different thing answering a different question, and nothing
    here should be mistaken for one.
    """

    written: int
    keys: int
    survivors: tuple[Claim, ...]
    overwritten: tuple[Claim, ...]

    def describe(self) -> str:
        return (
            f"{self.written} write(s) -> {self.keys} storage key(s) -> "
            f"{len(self.survivors)} survivor(s), "
            f"{len(self.overwritten)} overwritten"
        )

    def lines(self) -> tuple[str, ...]:
        """One line per claim, kept and lost, in a form a report can print."""
        kept = [f"kept  {c.value!r} asserted on {c.device_id}" for c in self.survivors]
        lost = [
            f"lost  {c.value!r} asserted on {c.device_id} -- replaced at the "
            "same key, unrecoverable from this store"
            for c in self.overwritten
        ]
        return tuple(kept + lost)


def naive_lww_survivors(
    writes: Iterable[Claim],
    key: Callable[[Claim], str] = logical_record_key,
) -> NaiveOutcome:
    """Run ``writes`` through the modelled strategy, in the order given.

    The order is the order the writes reached the single writer, and it is the
    only ordering this shape has: nothing in it reads ``observed_at``, because
    last-write-wins has no other comparison to make. A caller that wants the
    other outcome passes the writes the other way round, which is itself part of
    the finding — the surviving claim here is a function of arrival order, not
    of anything about the two observations.

    Re-sending a claim id the store already holds is idempotent rather than an
    overwrite, which is the one behaviour this shape gets right and the engine
    reproduces: a replay costs bytes and changes nothing.
    """
    claims = list(writes)
    current: dict[str, Claim] = {}
    overwritten: list[Claim] = []
    for claim in claims:
        slot = key(claim)
        displaced = current.get(slot)
        if displaced is not None and displaced.claim_id != claim.claim_id:
            overwritten.append(displaced)
        current[slot] = claim
    return NaiveOutcome(
        written=len(claims),
        keys=len(current),
        survivors=tuple(current.values()),
        overwritten=tuple(overwritten),
    )
