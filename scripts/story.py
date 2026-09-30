"""The scenario, in one place, run by everything that demonstrates the engine.

Two artefacts show this project working: the command-line demonstration and the
browser interface. When they each carried their own claims, they could drift, and
a demonstration that tells a different story from the test suite is worth nothing.
So the claims, the fleet, and the ordering of the steps live here and both
consumers import them.

The steps are cumulative by construction: reaching step *n* replays steps 1..n
from a clean fleet. That is what lets an operator jump to the disagreement
without the interface having to defend against being clicked twice, and it is why
the interface can offer any step as an entry point rather than only the next one.

Every verdict the interface shows is produced by calling :meth:`EdgeMemory.ask`.
There is no second read path, no authored demo data, and no number in the
interface that the engine did not measure during the run.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fixtures.trucks as trucks  # noqa: E402
from edgemem.domain import (  # noqa: E402
    AuthorityClass,
    CausalContext,
    Claim,
    Verdict,
    utcnow,
)
from edgemem.memory import EdgeMemory  # noqa: E402
from edgemem.outbox import Outbox  # noqa: E402
from edgemem.store import ShardStore  # noqa: E402
from edgemem.sync import Depot, DeviceLink, SyncReport  # noqa: E402

TRUCK = "TRK-7"
DOCK = "DEPOT-2"
DEPOT_ID = "DEPOT-1"

SEAL_SUBJECT = "trailer T-114 seal"
REEFER_SUBJECT = "reefer probe"
UNRELATED = "fuel card for tractor 12"


@dataclass
class Ask:
    """One question asked of one device, and the verdict it produced."""

    device_id: str
    question: str
    verdict: Verdict
    subject: str | None = None
    """What the caller told the device it was asking about, if anything.

    ``None`` means the engine worked it out alone, which is the honest and much
    weaker path. The scripted steps name their subject because an operator who
    has just read a reefer probe knows which reefer probe they mean; leaving the
    inference to rank two claims against each other would be testing something
    the demonstration is not about.
    """

    def to_payload(self) -> dict:
        return {
            "device_id": self.device_id,
            "question": self.question,
            "verdict": self.verdict.to_payload(),
        }


@dataclass
class StepResult:
    """What a step did: the questions it asked, the exchanges it performed."""

    asks: list[Ask] = field(default_factory=list)
    exchanges: list[SyncReport] = field(default_factory=list)


def _prior() -> CausalContext:
    """The state both devices believed before either of them left coverage.

    Both claims are stamped from this one vector, which is what makes them
    concurrent rather than one a revision of the other. A revision would not be
    a disagreement, and the whole demonstration rests on the difference.
    """
    return CausalContext({TRUCK: 1, DOCK: 1})


def _write(context: CausalContext, device_id: str) -> CausalContext:
    ctx = context.copy_for_write(device_id)
    ctx.observe(device_id)
    return ctx


def hand_seal(prior: CausalContext) -> Claim:
    """What the driver wrote by hand, out of coverage, with no instrument."""
    return Claim(
        subject=SEAL_SUBJECT,
        attribute="state",
        value="broken",
        author="operator-7",
        observer="handheld",
        device_id=TRUCK,
        observed_at=utcnow(),
        recorded_at=utcnow(),
        causal=_write(prior, TRUCK),
        source_class=AuthorityClass.UNATTESTED_HUMAN,
        salience=0.9,
        urgency=0.9,
    )


def dock_seal(prior: CausalContext) -> Claim:
    """What the dock scanner read, in the same dead zone, and cannot both be true of."""
    return Claim(
        subject=SEAL_SUBJECT,
        attribute="state",
        value="intact",
        author="dock operator 2",
        observer="dock-scanner",
        device_id=DOCK,
        observed_at=utcnow(),
        recorded_at=utcnow(),
        causal=_write(prior, DOCK),
        source_class=AuthorityClass.INSTRUMENT,
        salience=0.9,
        urgency=0.9,
    )


def reefer_setpoint(device_id: str) -> Claim:
    """A record the device received from elsewhere, on a different aspect.

    Recorded by whichever device carries it, because what matters is not who
    authored the record but that it travelled: a claim which arrived over the
    uplink is one the device believes is already at the depot, and such a claim
    is not one the device may answer from. That is the whole mechanism the
    withholding beat rests on, and it is reached only by an exchange.

    The aspect is deliberately not ``temperature``. A different attribute can
    never disagree with the reading, so this baseline cannot turn into a second,
    unrelated conflict and quietly blur what the seal disagreement means.

    Salience and urgency are set high because a record already filed at the depot
    is genuinely both, and because the policy scores those as a weighted pull
    toward the depot: below them the claim is held locally, never reaches the
    ledger, and the demonstration's first exchange would carry nothing at all.
    """
    return Claim(
        subject=REEFER_SUBJECT,
        attribute="contracted set-point",
        value="contracted set-point 4.0C, received from the depot on the previous leg",
        author=DEPOT_ID,
        observer="depot-feed",
        device_id=device_id,
        observed_at=utcnow(),
        recorded_at=utcnow(),
        causal=CausalContext({DEPOT_ID: 1, device_id: 1}),
        source_class=AuthorityClass.THIRD_PARTY_FEED,
        sensitivity=0.10,
        salience=0.70,
        urgency=0.60,
    )


def reefer_reading() -> str:
    """The reading the dock instrument took, which nothing else contradicts.

    Wording is identical to the operator's own note in an earlier draft of this
    scenario. That was a mistake worth recording: two devices holding the same
    value is corroboration, and the engine then cites both, and the summary
    repeats the same sentence twice. The demonstration should show corroboration
    working, not the seam's phrasing under a case it was not written for.
    """
    return "probe reads 3.8C, within range"


def reefer_held(prior: CausalContext) -> Claim:
    """The operator's own note about the reefer reading, taken by hand.

    Sensitivity is a veto in this policy, and a veto means the claim stays on the
    device for good rather than being weighed against anything. So this claim is
    answerable locally, which is exactly what makes it a poor candidate for the
    withholding beat: a device that is forbidden to send a claim is not thereby
    forbidden to answer from it, and confusing the two would misreport the engine.
    """
    return Claim(
        subject=REEFER_SUBJECT,
        attribute="temperature",
        value=reefer_reading(),
        author="operator-7",
        observer="handheld",
        device_id=TRUCK,
        observed_at=utcnow(),
        recorded_at=utcnow(),
        causal=_write(prior, TRUCK),
        source_class=AuthorityClass.UNATTESTED_HUMAN,
        sensitivity=0.97,
        salience=0.6,
        urgency=0.7,
    )


def reefer_scanned(prior: CausalContext) -> Claim:
    """The same reading from a calibrated instrument, low enough to leave the device.

    Nothing about the reading changed. What changed is whose observation this is:
    the dock scanner measured the probe itself and names no customer, so nothing
    about it is vetoed and the claim is permitted to reach the depot and answer.
    """
    return Claim(
        subject=REEFER_SUBJECT,
        attribute="temperature",
        value=reefer_reading(),
        author="dock operator 2",
        observer="dock-scanner",
        device_id=DOCK,
        observed_at=utcnow(),
        recorded_at=utcnow(),
        causal=_write(prior, DOCK),
        source_class=AuthorityClass.INSTRUMENT,
        sensitivity=0.40,
        salience=0.6,
        urgency=0.7,
    )


class Fleet:
    """Two devices and the depot between them, over a real socket."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.shards: list[ShardStore] = []
        self.devices: dict[str, EdgeMemory] = {}
        self.links: dict[str, DeviceLink] = {}
        self.link_up = True

        depot_memory = self._device(DEPOT_ID, url="")
        self.depot = Depot(
            depot_memory, self.root / "ledger.jsonl", depot_id=DEPOT_ID
        )
        self.depot_url = self.depot.serve()
        for device_id in (TRUCK, DOCK):
            self._device(device_id, url=self.depot_url)

    def _device(self, device_id: str, url: str) -> EdgeMemory:
        store = ShardStore(
            self.root / f"{device_id}-shard",
            device_id=device_id,
            dense_size=256,
        )
        store.open()
        self.shards.append(store)
        memory = EdgeMemory(
            store, pack=trucks.PACK, outbox=Outbox(self.root / f"{device_id}-outbox")
        )
        self.devices[device_id] = memory
        self.links[device_id] = DeviceLink(
            memory,
            memory.outbox,
            url,
            self.root / f"{device_id}-cursor.json",
            depot_id=DEPOT_ID,
        )
        return memory

    def ask(
        self, device_id: str, question: str, subject: str | None = None
    ) -> Ask:
        """Ask one device a question, and get back whatever it came back with.

        With no subject the engine infers one from what the device holds, and
        declines outright when nothing stands out. That inference is deliberately
        conservative, but it is still a guess, and the free-ask path in the
        interface exists so that the guess can be seen rather than trusted.
        """
        verdict = self.devices[device_id].ask(question, subject=subject)
        return Ask(
            device_id=device_id, question=question, verdict=verdict, subject=subject
        )

    def settle(self) -> None:
        """One exchange before the demonstration starts, to leave a held record.

        The dock carries a record it received from the depot and pushes it; the
        truck pulls it. The truck then decides, explicitly, that the copy it
        pulled is no longer the one to answer from because the depot holds the
        filed record.

        That last line is the caller's decision and not something the sync does on
        its own, because pulling a claim is not the same as losing the right to
        answer from it: the engine deliberately leaves an arriving claim
        answerable, since a device that can no longer answer from a record it is
        holding is worse than one that could. A fleet that archives at the depot
        says so here, and that is what makes the withholding beat reachable.
        """
        filed = reefer_setpoint(DOCK)
        self.devices[DOCK].record(filed)
        self.links[DOCK].sync()
        self.links[TRUCK].sync()
        self.devices[TRUCK].mark_present_at_depot([filed.claim_id])

    def reconnect(self) -> list[SyncReport]:
        """The uplink returns, and the delta is exchanged until both ends converge.

        Two rounds, and the second is not decoration. One round cannot carry both
        directions: whichever device syncs first has nothing yet to pull, so its
        counterpart arrives only on the following exchange. A real device that
        reconnects converges the same way, and the duplicate counts on the second
        round are the evidence that the first one was received rather than
        re-sent from scratch.
        """
        self.link_up = True
        reports: list[SyncReport] = []
        for _ in range(2):
            for device_id in (DOCK, TRUCK):
                reports.append(self.links[device_id].sync())
        return reports

    def close(self) -> None:
        self.depot.stop()
        for store in self.shards:
            store.close()
            store.destroy()


def _step_seed(fleet: Fleet) -> StepResult:
    """Everything recorded while neither device could reach the other."""
    fleet.settle()
    fleet.link_up = False
    prior = _prior()
    fleet.devices[TRUCK].record(hand_seal(prior))
    fleet.devices[DOCK].record(dock_seal(prior))
    fleet.devices[DOCK].record(reefer_scanned(prior))
    return StepResult()


def _step_unresolved(fleet: Fleet) -> StepResult:
    """The device holds a record it is not permitted to use, and says so."""
    return StepResult(
        asks=[fleet.ask(TRUCK, "reefer probe temperature", subject=REEFER_SUBJECT)]
    )


def _step_answered(fleet: Fleet) -> StepResult:
    """Alone and out of coverage, the dock device answers with no hesitation."""
    return StepResult(asks=[fleet.ask(DOCK, "seal state", subject=SEAL_SUBJECT)])


def _step_reconnect(fleet: Fleet) -> StepResult:
    """The uplink comes back and the delta moves, in both directions."""
    return StepResult(exchanges=fleet.reconnect())


def _step_conflicted(fleet: Fleet) -> StepResult:
    """The same question, asked again, and the engine refuses to choose."""
    return StepResult(asks=[fleet.ask(DOCK, "seal state", subject=SEAL_SUBJECT)])


def _step_corrected(fleet: Fleet) -> StepResult:
    """The question it declined earlier it can now answer, and says what changed."""
    return StepResult(
        asks=[fleet.ask(TRUCK, "reefer probe temperature", subject=REEFER_SUBJECT)]
    )


@dataclass(frozen=True)
class StoryStep:
    """A step an operator can enter directly, and what watching it shows."""

    n: int
    title: str
    note: str
    run: Callable[[Fleet], StepResult]
    expects: str | None = None
    """The verdict kind this step is meant to produce, when it produces one.

    Used by the interface to say what it believes it is about to show, and by the
    test to check that it showed it. Never used to render a verdict.
    """

    @property
    def short(self) -> str:
        return f"{self.n:02d}"


STEPS: tuple[StoryStep, ...] = (
    StoryStep(
        n=1,
        title="Out of coverage",
        note=(
            "Both devices record, neither can reach the other, and both stamp "
            "their claims from the same prior state. That is what makes them "
            "concurrent rather than one a revision of the other."
        ),
        run=_step_seed,
    ),
    StoryStep(
        n=2,
        title="Held, not answered",
        note=(
            "The truck asks about its own reefer. It holds a record about it that "
            "it believes is already at the depot, so it may not answer from it. It "
            "says what is missing and what it would cost to send."
        ),
        run=_step_unresolved,
        expects="UNRESOLVED_CLOUD_REQUIRED",
    ),
    StoryStep(
        n=3,
        title="Answered alone",
        note=(
            "The dock asks about the seal with no uplink at all, and answers "
            "confidently from one claim. It has no way to know it is wrong."
        ),
        run=_step_answered,
        expects="ANSWERED_LOCALLY",
    ),
    StoryStep(
        n=4,
        title="The uplink returns",
        note=(
            "Both devices exchange what they held. The path that actually ran is "
            "reported rather than assumed, and the cursor says how much of the "
            "ledger each had already seen."
        ),
        run=_step_reconnect,
    ),
    StoryStep(
        n=5,
        title="Both survive",
        note=(
            "The same question now returns a disagreement. Neither claim is "
            "overwritten, the ladder is presented rather than applied, and the "
            "engine says plainly that it is not choosing."
        ),
        run=_step_conflicted,
        expects="CONFLICTED",
    ),
    StoryStep(
        n=6,
        title="What changed its mind",
        note=(
            "The truck asks the question it declined. It can answer now, it can "
            "show what it said last time, and it names the single claim that "
            "moved it, because exactly one did."
        ),
        run=_step_corrected,
        expects="CORRECTED",
    ),
)


"""A seventh step was cut, and the reason is worth keeping.

It was going to ask about something neither device held, to show the engine
declining rather than guessing. Asking does not produce that: with any claim on
the device, ``_infer_subject`` always resolves to some subject, so an unrelated
question comes back answered about the nearest claim instead of refused. Asking
about a fuel card returned the trailer seal's disagreement.

That is a real weakness in the seam, not a defect in the step, so the step was
removed instead of being relabelled as a feature. The guided story covers all
four verdicts without it. The free-ask path in the interface still sends no
subject and still shows which one the engine inferred, so the guess is visible
rather than hidden.
"""


def run_to(root: Path, n: int) -> tuple[Fleet, list[tuple[StoryStep, StepResult]]]:
    """Replay the scenario from a clean fleet up to and including step ``n``.

    Cumulative on purpose. Entering at step 6 produces the same device state as
    running 1 through 6 in order, because it runs 1 through 6 in order.
    """
    if not 1 <= n <= len(STEPS):
        raise ValueError(f"step {n} is outside 1..{len(STEPS)}")
    fleet = Fleet(Path(root))
    performed: list[tuple[StoryStep, StepResult]] = []
    for step in STEPS[:n]:
        performed.append((step, step.run(fleet)))
    return fleet, performed


__all__ = [
    "Ask",
    "DEPOT_ID",
    "DOCK",
    "Fleet",
    "REEFER_SUBJECT",
    "SEAL_SUBJECT",
    "STEPS",
    "StoryStep",
    "StepResult",
    "TRUCK",
    "UNRELATED",
    "dock_seal",
    "hand_seal",
    "reefer_held",
    "reefer_reading",
    "reefer_scanned",
    "run_to",
]