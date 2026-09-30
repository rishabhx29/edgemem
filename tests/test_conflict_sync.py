"""A disagreement that survives a real sync round trip, and a restart.

The scenario is deliberately narrow, and deliberately honest about being narrow:
two devices that cannot reach each other, one claim each, written from the same
prior causal state so that neither saw the other. The only thing that puts them
in touch is a sync over a socket, so every claim in a verdict below either was
recorded on the device that made it or arrived over that socket. No test here
writes a claim into a store to stand in for a round trip.

Everything asserted is something a caller observes: the fields of a
:class:`Verdict`, the fields of a :class:`SyncReport`, and what a question
returns before and after a restart.

The concurrency is not implied by the devices being separate. Two claims are
concurrent exactly when neither causal context dominates the other, so each
claim here is stamped from one shared prior advanced by its own device's write
and nothing else. A claim written after seeing the other carries the other
device's counter as well, and is a revision rather than a conflict; the second
group below holds that half of the rule in place against a live sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import edgemem
import pytest
from edgemem.comparison import naive_lww_survivors
from edgemem.domain import (
    AuthorityClass,
    CausalContext,
    Claim,
    Trust,
    Verdict,
    VerdictKind,
    iso,
)
from edgemem.memory import EdgeMemory
from edgemem.outbox import Outbox
from edgemem.store import ShardStore
from edgemem.sync import Depot, DeviceLink, SyncPath, SyncReport
from fixtures import trucks

SUBJECT = "trailer T-114 seal"
ATTRIBUTE = "state"
QUESTION = "seal state"

HAND_ID = "TRK-7"
"""The driver's handheld. A note, not a measurement."""

DOCK_ID = "DOCK-2"
"""The dock's calibrated scanner. A measurement, and ranked as one."""

PRIOR = CausalContext({HAND_ID: 1, DOCK_ID: 1})
"""The history both devices shared before they went out of coverage.

The point of the whole file. Each claim is stamped from this same prior advanced
by its own device's counter alone, so neither context dominates the other and
the two claims are concurrent by construction rather than by assertion. Two
separate devices are not sufficient: two claims written in sequence on one
device are just a revision, and the engine is right to treat them as one.
"""

SCANNED_AT = datetime(2026, 3, 14, 9, 12, tzinfo=timezone.utc)
NOTED_AT = datetime(2026, 3, 14, 9, 15, tzinfo=timezone.utc)
"""Fixed moments, and deliberately not in the order the ladder prefers.

The scanner read the seal three minutes before the driver wrote his note, so the
later observation of the two is the one the ladder ranks lower. An escalation
that followed recency would name the driver, and the ladder's not doing so is
what makes "recency is not an input" a fact about the verdict rather than a claim
about the source.
"""


# --------------------------------------------------------------------------
# the two honest devices
# --------------------------------------------------------------------------


def _claim(**over: object) -> Claim:
    now = datetime(2026, 3, 14, 9, 0, tzinfo=timezone.utc)
    base: dict[str, object] = {
        "subject": SUBJECT,
        "attribute": ATTRIBUTE,
        "value": "broken",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": HAND_ID,
        "observed_at": now,
        "recorded_at": now,
        "salience": 0.9,
        "urgency": 0.9,
    }
    base.update(over)
    return Claim(**base)  # type: ignore[arg-type]


def _offline(claim: Claim, device_id: str, prior: CausalContext = PRIOR) -> Claim:
    """``claim`` as this device recorded it while it could not reach anyone.

    The context is the shared prior advanced by this device's own write and
    nothing else. Deliberately constructed rather than read off the claim: the
    engine decides concurrency from this field, and a scenario that left it at
    its default would be testing a coincidence.
    """
    causal = prior.copy_for_write(device_id)
    causal.observe(device_id)
    return replace(claim, causal=causal)


def hand_note(**over: object) -> Claim:
    defaults: dict[str, object] = {
        "value": "broken",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": HAND_ID,
        "observed_at": NOTED_AT,
        "recorded_at": NOTED_AT,
        "source_class": AuthorityClass.UNATTESTED_HUMAN,
    }
    return _offline(_claim(**{**defaults, **over}), HAND_ID)


def dock_reading(**over: object) -> Claim:
    defaults: dict[str, object] = {
        "value": "intact",
        "author": "dock operator 2",
        "observer": "dock-scanner",
        "device_id": DOCK_ID,
        "observed_at": SCANNED_AT,
        "recorded_at": SCANNED_AT,
        "source_class": AuthorityClass.INSTRUMENT,
    }
    return _offline(_claim(**{**defaults, **over}), DOCK_ID)


def sides_of(verdict: Verdict) -> set[tuple[str, str]]:
    """Which value each side asserts, and which device asserted it."""
    return {
        (side.claim.value, side.claim.device_id)
        for pair in verdict.conflicts
        for side in pair
    }


# --------------------------------------------------------------------------
# fixtures: a depot and two devices, all real
# --------------------------------------------------------------------------


@dataclass
class Fleet:
    """A depot, two devices, and the bookkeeping to tear all three down.

    Held as one object so a test cannot leak a shard by forgetting something.
    Every shard opened through here is closed and destroyed by the fixture that
    built it, including the one a restart replaced.
    """

    root: Path
    depot: Depot
    hand: EdgeMemory
    dock: EdgeMemory
    opened: list[ShardStore] = field(default_factory=list)
    outboxes: dict[str, Path] = field(default_factory=dict)

    @property
    def by_id(self) -> dict[str, EdgeMemory]:
        return {self.hand.device_id: self.hand, self.dock.device_id: self.dock}

    def reconnect(self, device: EdgeMemory) -> SyncReport:
        """One real reconnect: a real socket, a real ledger, a real pull.

        The retained position is keyed by device, so a device's second reconnect
        resumes from where the first stopped rather than starting the transfer
        again. That is what makes the cursor fields in the report mean anything.
        """
        return DeviceLink(
            device,
            device.outbox,
            self.depot.url,
            self.root / f"cursor-{device.device_id}.json",
        ).sync()

    def restart(self, device: EdgeMemory) -> EdgeMemory:
        """Close the shard and open a new device on the same directory.

        What a process restart does. Nothing in memory carries across, so
        anything the new device can still answer came off the disk rather than
        out of a queue: the absorbed claim has no outbox entry to be replayed
        from, and it is still there.
        """
        path = device.store.path
        device.store.close()
        store = ShardStore(path, device_id=device.device_id)
        store.open()
        self.opened.append(store)
        return EdgeMemory(
            store,
            pack=device.pack,
            outbox=Outbox(self.outboxes[device.device_id]),
        )


@pytest.fixture
def fleet(tmp_path):
    opened: list[ShardStore] = []
    outboxes: dict[str, Path] = {}

    def device(device_id: str) -> EdgeMemory:
        store = ShardStore(tmp_path / f"shard-{device_id}", device_id=device_id)
        store.open()
        opened.append(store)
        outboxes[device_id] = tmp_path / f"outbox-{device_id}.jsonl"
        return EdgeMemory(store, pack=trucks.PACK, outbox=Outbox(outboxes[device_id]))

    depot = Depot(device("DEPOT-1"), tmp_path / "depot-ledger.jsonl", depot_id="DEPOT-1")
    depot.serve()
    built = Fleet(
        root=tmp_path,
        depot=depot,
        hand=device(HAND_ID),
        dock=device(DOCK_ID),
        opened=opened,
        outboxes=outboxes,
    )
    try:
        yield built
    finally:
        depot.stop()
        for store in opened:
            store.destroy()


def record_apart(fleet: Fleet, *claims: Claim) -> None:
    """Each claim goes to the device that made it. Nothing is shared yet."""
    for claim in claims:
        fleet.by_id[claim.device_id].record(claim)


def reconnect_both(fleet: Fleet) -> tuple[SyncReport, SyncReport]:
    """Both devices come back into coverage, one after the other.

    The truck reconnects first, so the scanner's claim is written into the store
    after the truck's own. Arrival order at the depot is the reverse of
    observation order, which matters for the comparison in the last group.
    """
    return fleet.reconnect(fleet.hand), fleet.reconnect(fleet.dock)


# --------------------------------------------------------------------------
# the ticket's scenario
# --------------------------------------------------------------------------


def test_two_offline_devices_reach_a_conflict_and_it_survives_a_restart(fleet):
    """Two honest devices, one real link, and a verdict that outlives the process.

    Each device is asked the question while it is still alone, so the conflict
    below is shown to be something the round trip produced rather than something
    that was there before it.
    """
    hand = hand_note()
    dock = dock_reading()
    assert hand.causal.is_concurrent_with(dock.causal), (
        "the two claims are not concurrent, so nothing here would be a conflict"
    )

    record_apart(fleet, hand, dock)
    assert fleet.hand.ask(QUESTION, subject=SUBJECT).kind is VerdictKind.ANSWERED_LOCALLY
    assert fleet.dock.ask(QUESTION, subject=SUBJECT).kind is VerdictKind.ANSWERED_LOCALLY

    up, down = reconnect_both(fleet)

    assert up.path == down.path == down.depot_path == SyncPath.DEPOT_DELTA.value
    assert (up.pushed, up.accepted, up.duplicates, up.pulled, up.withheld) == (
        1,
        1,
        0,
        0,
        0,
    ), "the truck's claim did not reach the depot on its own"
    assert (down.pushed, down.accepted, down.duplicates, down.pulled) == (
        1,
        1,
        0,
        1,
    ), "the scanner's claim did not reach the depot, or the truck's did not come down"
    assert up.complete and down.complete
    assert down.reapplied == 0, "a claim the device already held was replaced"
    assert down.bytes_received > 0, "the pull cost nothing, which cannot be true"

    verdict = fleet.dock.ask(QUESTION, subject=SUBJECT)
    assert verdict.kind is VerdictKind.CONFLICTED
    assert sides_of(verdict) == {("broken", HAND_ID), ("intact", DOCK_ID)}
    assert {c.claim_id for c in fleet.dock.memory()} == {hand.claim_id, dock.claim_id}, (
        "the round trip cost one of the two claims"
    )

    restarted = fleet.restart(fleet.dock)
    after = restarted.ask(QUESTION, subject=SUBJECT)

    assert after.kind is VerdictKind.CONFLICTED
    assert sides_of(after) == sides_of(verdict)
    assert {s.claim.claim_id for pair in after.conflicts for s in pair} == {
        hand.claim_id,
        dock.claim_id,
    }, "a claim survived the round trip but not the restart"
    assert after.summary == verdict.summary


def test_both_claims_keep_their_attribution_across_the_round_trip(fleet):
    """What a caller can do with a side of a conflict is what it was.

    An absorbed claim re-stamped as this device's own observation would keep both
    sides in the verdict and lose the disagreement: both would then carry one
    causal context, neither would be concurrent with the other, and the engine
    would report two claims rather than a conflict.
    """
    hand = hand_note()
    dock = dock_reading()
    record_apart(fleet, hand, dock)
    reconnect_both(fleet)

    verdict = fleet.dock.ask(QUESTION, subject=SUBJECT)
    arrived = {s.claim.claim_id: s.claim for pair in verdict.conflicts for s in pair}

    assert set(arrived) == {hand.claim_id, dock.claim_id}, "a claim lost its identity"
    for original in (hand, dock):
        citation = arrived[original.claim_id]
        assert citation.author == original.author
        assert citation.observer == original.observer
        assert citation.device_id == original.device_id
        assert citation.observed_at == iso(original.observed_at)
        assert citation.source_class == original.source_class.value
        assert citation.trust == Trust.TRUSTED.value
        assert citation.residency, "a citation must say why it was usable here"


def test_the_escalation_presents_the_ladder_and_resolves_nothing(fleet):
    """The vertical's ordering, the side entitled under it, and no decision.

    The driver's note is the later of the two observations, so an escalation
    following recency would name him. The ladder names the scanner, and both
    claims are still in the device's memory afterwards.
    """
    hand = hand_note()
    dock = dock_reading()
    assert hand.observed_at > dock.observed_at, "the later claim must be the lower one"
    record_apart(fleet, hand, dock)
    reconnect_both(fleet)

    verdict = fleet.dock.ask(QUESTION, subject=SUBJECT)

    assert verdict.authority is not None
    assert verdict.authority.ladder_version == trucks.PACK.ladder.version
    assert verdict.authority.ordered_classes == trucks.PACK.ladder.ordered()
    assert verdict.authority.entitling_class == AuthorityClass.INSTRUMENT.value
    assert verdict.authority.note == trucks.PACK.ladder.note
    assert "not choosing" in verdict.summary.lower()

    entitling = [s for pair in verdict.conflicts for s in pair if s.entitling]
    assert len(entitling) == 1, "the escalation named no side, or named both"
    assert entitling[0].claim.source_class == AuthorityClass.INSTRUMENT.value
    assert verdict.claims, "a conflict must also cite what it is about"

    # Presented is not applied. Nothing was deleted to make the escalation tidy.
    assert {c.claim_id for c in fleet.dock.memory()} == {hand.claim_id, dock.claim_id}


def test_corroboration_is_counted_for_each_side_separately(fleet):
    """Two witnesses on the trailer, one reading on the dock.

    The count is per side rather than a total, because a total says only that
    people disagreed. Which side is better supported is the fact a person
    settling it actually needs.
    """
    first = hand_note()
    second = hand_note(author="operator-11", observer="driver-report")
    dock = dock_reading()
    record_apart(fleet, first, second, dock)

    up, down = reconnect_both(fleet)

    assert (up.pushed, down.pulled) == (2, 2), "the second witness did not travel"
    verdict = fleet.dock.ask(QUESTION, subject=SUBJECT)
    assert verdict.kind is VerdictKind.CONFLICTED
    assert len(verdict.conflicts) == 2, (
        "each disagreeing claim must be paired with the claim it disagrees with"
    )
    for a, b in verdict.conflicts:
        assert {a.claim.value: a.supporters, b.claim.value: b.supporters} == {
            "broken": 2,
            "intact": 1,
        }


# --------------------------------------------------------------------------
# what is not a conflict
# --------------------------------------------------------------------------


def test_a_supersession_that_saw_the_earlier_claim_is_not_a_conflict(fleet):
    """A claim written after seeing the other is a revision, and is shown as one.

    The revised claim carries the earlier claim's causal state as well as its own
    write, so it dominates rather than running alongside. Without that, every
    correction a device made after a sync would be escalated to a person as a
    disagreement, and the escalation would mean nothing.
    """
    original = hand_note(value="intact")
    fleet.hand.record(original)
    fleet.reconnect(fleet.hand)
    fleet.reconnect(fleet.dock)

    before = fleet.dock.ask(QUESTION, subject=SUBJECT)
    assert before.kind is VerdictKind.ANSWERED_LOCALLY
    assert [c.value for c in before.claims] == ["intact"]

    seen = original.causal.merged_with(CausalContext())
    seen.observe(DOCK_ID)
    revised = replace(
        dock_reading(
            value="broken",
            author="dock operator 2",
            observer="handheld",
            source_class=AuthorityClass.UNATTESTED_HUMAN,
        ),
        causal=seen,
        supersedes=original.claim_id,
    )
    assert revised.causal.dominates(original.causal), (
        "the revision does not carry what it saw, so the two would be concurrent"
    )

    fleet.dock.record(revised)
    after = fleet.dock.ask(QUESTION, subject=SUBJECT)

    assert after.kind is VerdictKind.CORRECTED
    assert after.conflicts == ()
    assert after.authority is None, "a revision has nobody to escalate to"
    assert after.previous_summary == before.summary
    # `changed_by` names the claim the new answer is built from. It does not
    # claim to be the claim that caused the change, so this asserts that it
    # names one of the two rather than that it names the revision.
    assert after.changed_by in {original.claim_id, revised.claim_id}
    assert {c.value for c in after.claims} == {"intact", "broken"}
    # A revision is recorded, not performed: the earlier claim is still readable.
    assert original.claim_id in {c.claim_id for c in fleet.dock.memory()}


def test_a_quarantined_claim_does_not_create_a_conflict(fleet):
    """A claim nobody vouches for is held, not weighed.

    Quarantine is a statement about the source, so it takes the claim out of the
    comparison entirely. The trusted claim answers, and the untrusted one is
    named as something the device will not use, with the reason.
    """
    hand = hand_note()
    dock = dock_reading().with_trust(Trust.QUARANTINED, "unattributed dock feed")
    record_apart(fleet, hand, dock)
    reconnect_both(fleet)

    verdict = fleet.dock.ask(QUESTION, subject=SUBJECT)

    assert verdict.kind is VerdictKind.ANSWERED_LOCALLY
    assert verdict.conflicts == ()
    assert verdict.authority is None
    assert [c.value for c in verdict.claims] == ["broken"]
    assert [n.claim_id for n in verdict.needed] == [dock.claim_id]
    assert verdict.needed[0].why_withheld == "quarantined: unattributed dock feed"
    # Quarantine is a judgement, not a deletion.
    assert dock.claim_id in {c.claim_id for c in fleet.dock.memory()}


# --------------------------------------------------------------------------
# the comparison: evidence, and evidence that it is only evidence
# --------------------------------------------------------------------------


def test_one_strategy_keeps_both_concurrent_writes_and_the_other_keeps_one(fleet):
    """The argument this repository is making, measured instead of asserted.

    The same two claims, recorded by the same two offline devices from the same
    prior state, are put through the real link and through the modelled
    single-writer strategy. What differs is merge semantics: one storage key for
    two concurrent writes leaves one of them gone, and the engine still holds it.
    """
    hand = hand_note()
    dock = dock_reading()
    record_apart(fleet, hand, dock)
    reconnect_both(fleet)

    verdict = fleet.dock.ask(QUESTION, subject=SUBJECT)
    kept = {s.claim.claim_id for pair in verdict.conflicts for s in pair}

    # Arrival order at the single writer is the order the depot saw the writes.
    outcome = naive_lww_survivors([hand, dock])

    assert verdict.kind is VerdictKind.CONFLICTED
    assert kept == {hand.claim_id, dock.claim_id}
    assert outcome.keys == 1, "the two claims were not written to one record"
    assert [c.claim_id for c in outcome.survivors] == [dock.claim_id]
    assert [c.claim_id for c in outcome.overwritten] == [hand.claim_id]
    assert {c.claim_id for c in outcome.survivors} < kept, (
        "a claim the naive store lost is one the engine still holds"
    )


def test_the_comparison_is_not_reachable_from_any_engine_module():
    """Evidence the engine could import would stop being evidence.

    Read from the installed package rather than from a list of modules, so a new
    engine module cannot quietly reach the evidence without this noticing.
    """
    engine_root = Path(edgemem.__file__).resolve().parent
    modules = [
        path
        for path in engine_root.rglob("*.py")
        if "__pycache__" not in path.parts and path.name != "comparison.py"
    ]
    assert len(modules) >= 5, f"no engine modules found under {engine_root}"
    for module in modules:
        assert "comparison" not in module.read_text(encoding="utf-8"), (
            f"{module.name} refers to the evidence module"
        )

    assert not [name for name in edgemem.__all__ if "comparison" in name]
    assert edgemem.comparison.__doc__.startswith("EVIDENCE ONLY")
    assert set(edgemem.comparison.__all__) == {
        "NaiveOutcome",
        "logical_record_key",
        "naive_lww_survivors",
    }
