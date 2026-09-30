"""The same two concurrent claims, through this engine and through a naive one.

Prints both strategies' surviving claim sets from one run, so the difference
between them is something a reader can look at rather than something they have
to take on trust. The engine's side is measured through its own seam — a depot
behind a real socket, two devices on two real shards, one reconnect each — and
the naive side is the modelled strategy in :mod:`edgemem.comparison`, which is
evidence and is imported by nothing else.

Run with ``.\\.venv\\Scripts\\python.exe scripts\\demo_comparison.py``.

**The scope of the claim, stated as narrowly as the evidence allows.** What the
two columns differ on is merge semantics. A single-writer, last-write-wins,
point-id-keyed store has one storage key for a logical record, so two concurrent
writes to that record leave one of them overwritten and unrecoverable from that
store. This is *not* a claim that the vendor's documented pattern loses data or
destroys unsynced writes: that vendor's reference implementation flushes before
it sets its watermark, so a device that has synced keeps what it synced. The
difference being printed below is that the engine can hold two concurrent
versions of one record at once, and report that they disagree.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src",):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from edgemem.comparison import NaiveOutcome, naive_lww_survivors
from edgemem.domain import (
    AuthorityClass,
    CausalContext,
    Claim,
    ConflictSide,
    Verdict,
    iso,
)
from edgemem.memory import EdgeMemory
from edgemem.outbox import Outbox
from edgemem.store import ShardStore
from edgemem.sync import Depot, DeviceLink, SyncReport

FIELD_ID = "FIELD-2"
"""A technician's handheld, working where there is no coverage."""

GATE_ID = "GATE-1"
"""A calibrated scanner on the gate, looking at the same thing."""

SUBJECT = "reactor feed pump P-3"
ATTRIBUTE = "seal state"

SCANNED_AT = datetime(2026, 3, 14, 9, 12, tzinfo=timezone.utc)
NOTED_AT = datetime(2026, 3, 14, 9, 15, tzinfo=timezone.utc)

# The two devices, with the field one ahead on the shared history and the gate
# behind it. A pair of contexts that neither dominates is the whole point, so it
# is built here rather than assumed.
PRIOR = CausalContext({FIELD_ID: 1, GATE_ID: 1})


def _base(**over: object) -> Claim:
    now = datetime(2026, 3, 14, 9, 0, tzinfo=timezone.utc)
    fields: dict[str, object] = {
        "subject": SUBJECT,
        "attribute": ATTRIBUTE,
        "value": "dry",
        "author": "field tech",
        "observer": "handheld",
        "device_id": FIELD_ID,
        "observed_at": now,
        "recorded_at": now,
        "salience": 0.9,
        "urgency": 0.9,
    }
    fields.update(over)
    return Claim(**fields)  # type: ignore[arg-type]


def _offline(claim: Claim, device_id: str) -> Claim:
    """Stamped from the shared prior advanced by this device's write alone."""
    causal = PRIOR.copy_for_write(device_id)
    causal.observe(device_id)
    return replace(claim, causal=causal)


def field_note() -> Claim:
    return _offline(
        _base(
            value="leaking",
            author="field tech R. Osei",
            observer="handheld",
            device_id=FIELD_ID,
            observed_at=NOTED_AT,
            recorded_at=NOTED_AT,
            source_class=AuthorityClass.UNATTESTED_HUMAN,
        ),
        FIELD_ID,
    )


def gate_reading() -> Claim:
    return _offline(
        _base(
            value="dry",
            author="gate console",
            observer="scanner SG-4",
            device_id=GATE_ID,
            observed_at=SCANNED_AT,
            recorded_at=SCANNED_AT,
            source_class=AuthorityClass.INSTRUMENT,
        ),
        GATE_ID,
    )


def describe_claim(claim: Claim) -> str:
    return (
        f"{claim.value!r} asserted by {claim.author} "
        f"(observed by {claim.observer} on {claim.device_id} at "
        f"{iso(claim.observed_at)})"
    )


def describe_side(side: ConflictSide) -> str:
    return f"{side.claim.value!r} by {side.claim.author} on {side.claim.device_id}"


# --------------------------------------------------------------------------
# the engine side, measured through the seam
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineSide:
    """What the engine's own question interface said, as plain data.

    Verdict and report objects rather than their fields, so nothing here has to
    be restated to be printed and nothing can drift from what actually came back.
    """

    reports: tuple[SyncReport, ...]
    alone: Verdict
    verdict: Verdict
    held_on_gate: int


def run_engine(root: Path) -> EngineSide:
    """Two offline devices, one depot, one reconnect each, then the question."""
    opened: list[ShardStore] = []

    def device(device_id: str) -> EdgeMemory:
        store = ShardStore(root / f"shard-{device_id}", device_id=device_id)
        store.open()
        opened.append(store)
        return EdgeMemory(
            store, outbox=Outbox(root / f"outbox-{device_id}.jsonl")
        )

    depot: Depot | None = None
    try:
        depot = Depot(
            device("DEPOT-1"), root / "depot-ledger.jsonl", depot_id="DEPOT-1"
        )
        depot.serve()
        field = device(FIELD_ID)
        gate = device(GATE_ID)

        field.record(field_note())
        gate.record(gate_reading())
        alone = gate.ask("seal state", subject=SUBJECT)

        reports = []
        for device_side in (field, gate):
            reports.append(
                DeviceLink(
                    device_side,
                    device_side.outbox,
                    depot.url,
                    root / f"cursor-{device_side.device_id}.json",
                ).sync()
            )

        gate.store.close()
        restarted = ShardStore(root / f"shard-{GATE_ID}", device_id=GATE_ID)
        restarted.open()
        opened.append(restarted)
        reopened = EdgeMemory(
            restarted, outbox=Outbox(root / f"outbox-{GATE_ID}.jsonl")
        )
        after_restart = reopened.ask("seal state", subject=SUBJECT)

        return EngineSide(
            reports=tuple(reports),
            alone=alone,
            verdict=after_restart,
            held_on_gate=len(reopened.memory()),
        )
    finally:
        if depot is not None:
            depot.stop()
        for store in opened:
            store.destroy()


# --------------------------------------------------------------------------
# the naive side, and the report
# --------------------------------------------------------------------------


def run_naive(hand: Claim, dock: Claim) -> NaiveOutcome:
    """The modelled strategy, in the order the depot saw the two writes.

    The field device reconnects first, so its claim reaches the single writer
    first and the gate's replaces it. The gate read the seal three minutes
    earlier, so what survives here is decided by arrival order rather than by
    anything about the two observations.
    """
    return naive_lww_survivors([hand, dock])


def report(engine: EngineSide, naive: NaiveOutcome) -> None:
    verdict = engine.verdict
    kept_by_engine = sorted({s.claim.value for pair in verdict.conflicts for s in pair})
    kept_by_naive = sorted(c.value for c in naive.survivors)

    print()
    print("=== edgemem: real depot, real socket, real shards ===")
    for sync in engine.reports:
        print(f"  {sync.device_id}: {sync.describe()}")
    print(
        f"  {GATE_ID} asked while still alone: {engine.alone.kind.value} "
        f"({engine.alone.summary})"
    )
    print(f"  asked again after both reconnects and a restart: {verdict.kind.value}")
    print(f"  {verdict.summary}")
    for a, b in verdict.conflicts:
        for side in (a, b):
            mark = "entitling" if side.entitling else "also retained"
            print(
                f"    {describe_side(side)} -- {side.supporters} supporter(s), "
                f"{mark}"
            )
    if verdict.authority is not None:
        print(
            f"  ladder {verdict.authority.ladder_version}: "
            f"{' > '.join(verdict.authority.ordered_classes)}"
        )
        print(f"  entitled to settle it: {verdict.authority.entitling_class}")
    print(f"  claims held on {GATE_ID}: {engine.held_on_gate}")
    print(f"  surviving claim values: {kept_by_engine}")

    print()
    print("=== naive single-writer last-write-wins, keyed by subject+attribute ===")
    print(f"  {naive.describe()}")
    for line in naive.lines():
        print(f"    {line}")
    print(f"  surviving claim values: {kept_by_naive}")

    print()
    print("=== the difference, and only the difference ===")
    only_engine = sorted(set(kept_by_engine) - set(kept_by_naive))
    print(f"  edgemem kept: {kept_by_engine}")
    print(f"  naive kept:   {kept_by_naive}")
    print(f"  only edgemem kept: {only_engine}")
    print(
        "  cause: the naive store has one storage key per logical record, so "
        f"its {naive.written} writes landed on {naive.keys} key and left "
        f"{len(naive.survivors)} survivor and {len(naive.overwritten)} "
        "overwritten, rather than holding both versions."
    )
    print(
        "  this is a statement about merge semantics. It is not a claim that "
        "the vendor's documented pattern loses data or drops unsynced writes: "
        "that reference implementation flushes before setting its watermark."
    )


def main() -> None:
    hand, dock = field_note(), gate_reading()

    print("=== the scenario: one subject, one attribute, two devices offline ===")
    print(f"  subject: {SUBJECT!r}  attribute: {ATTRIBUTE!r}")
    print(f"  shared prior causal state: {PRIOR.to_payload()}")
    for claim in (dock, hand):
        print(f"  {describe_claim(claim)}")
        print(f"    causal {claim.causal.to_payload()}")
    print(
        f"  concurrent: {hand.causal.is_concurrent_with(dock.causal)} "
        "(neither context dominates the other, so neither device saw the other)"
    )

    with tempfile.TemporaryDirectory(prefix="edgemem-comparison-") as scratch:
        engine = run_engine(Path(scratch))
        report(engine, run_naive(hand, dock))


if __name__ == "__main__":
    main()
