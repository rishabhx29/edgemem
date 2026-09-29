"""Unsent claims survive a restart.

The device is a separate operating-system process that is terminated without
warning, and the claims it recorded while the link was down are still there
afterwards and still answer questions. That is the difference between an
offline-first device and an offline-until-restarted one, and it cannot be
demonstrated by closing a handle: the test kills the process outright, which is
the only way to be certain nothing is being held in an address space that the
shutdown was supposed to have flushed.

Every assertion goes through ``ask``. Nothing here inspects the queue, the journal
on disk, or any member whose name begins with an underscore.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import edgemem
from edgemem.domain import Claim, utcnow
from edgemem.memory import EdgeMemory
from edgemem.outbox import Outbox
from edgemem.store import ShardStore
from edgemem.sync import Depot, DeviceLink

SUBJECT = "trailer T-114 seal"
ENGINE_PATH = str(Path(edgemem.__file__).resolve().parent.parent)

RECORD_THEN_DIE = textwrap.dedent(
    """
    import json, sys, time
    from datetime import timedelta
    from pathlib import Path

    from edgemem.domain import Claim, utcnow
    from edgemem.memory import EdgeMemory
    from edgemem.outbox import Outbox
    from edgemem.store import ShardStore

    shard, journal, count = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])

    store = ShardStore(shard, device_id="TRK-7")
    store.open()
    device = EdgeMemory(store, outbox=Outbox(journal))

    seen = utcnow()
    recorded = []
    for step in range(count):
        claim = Claim(
            subject="trailer T-114 seal",
            attribute="state",
            value=("intact", "broken", "resecured")[step % 3],
            author="operator-7",
            observer="handheld",
            device_id="TRK-7",
            observed_at=seen + timedelta(minutes=step),
            recorded_at=seen + timedelta(minutes=step),
            salience=0.9,
            urgency=0.9,
        )
        # Each reading follows the last, so these are a revision history rather
        # than three concurrent assertions about the same instant.
        for _ in range(step + 1):
            claim.causal.observe("TRK-7")
        recorded.append(device.record(claim).claim_id)

    print(json.dumps(recorded), flush=True)
    time.sleep(300)
    """
)


def claim(**over) -> Claim:
    now = utcnow()
    base = {
        "subject": SUBJECT,
        "attribute": "state",
        "value": "broken",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": "TRK-7",
        "observed_at": now,
        "recorded_at": now,
        "salience": 0.9,
        "urgency": 0.9,
    }
    base.update(over)
    return Claim(**base)


def open_device(root: Path, device_id: str = "TRK-7") -> tuple[EdgeMemory, ShardStore]:
    store = ShardStore(root / f"shard-{device_id}", device_id=device_id)
    store.open()
    return EdgeMemory(store, outbox=Outbox(root / f"outbox-{device_id}.jsonl")), store


def open_depot(root: Path) -> Depot:
    store = ShardStore(root / "shard-DEPOT-1", device_id="DEPOT-1")
    store.open()
    return Depot(EdgeMemory(store), root / "depot-ledger.jsonl")


def record_until_killed(root: Path, count: int) -> list[str]:
    """Start a real device process, wait for it to record, then terminate it.

    No shutdown, no flush, no atexit hook. The process dies the way a battery
    coming off a truck kills one, and the claim ids it managed to print are the
    ones the parent will look for afterwards.
    """
    script = root / "device_child.py"
    script.write_text(RECORD_THEN_DIE, encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(script),
            str(root / "shard-TRK-7"),
            str(root / "outbox-TRK-7.jsonl"),
            str(count),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": ENGINE_PATH},
    )
    try:
        line = proc.stdout.readline()
        if not line:
            proc.kill()
            raise AssertionError(
                f"the device process recorded nothing: {proc.stderr.read()[-2000:]}"
            )
        return json.loads(line)
    finally:
        proc.kill()
        proc.wait(timeout=30)


@pytest.fixture
def killed_device(tmp_path):
    """A device that recorded three readings and was then terminated."""
    return record_until_killed(tmp_path, 3)


@pytest.fixture
def depot(tmp_path):
    central = open_depot(tmp_path)
    central.serve()
    yield central
    central.stop()
    central.memory.store.destroy()


@pytest.fixture
def link_factory(tmp_path, depot):
    def build(device: EdgeMemory, name: str = "cursor") -> DeviceLink:
        return DeviceLink(
            device, device.outbox, depot.url, tmp_path / f"{name}.json"
        )

    return build


# --------------------------------------------------------------------------
# a real process kill
# --------------------------------------------------------------------------


def test_a_killed_device_still_answers_from_what_it_recorded(tmp_path, killed_device):
    """The ticket's acceptance test, run the only way it can honestly be run.

    A restart inside one process would prove only that a handle was closed. A
    terminated process proves that what the device recorded is on the disk and not
    in an address space.
    """
    assert len(killed_device) == 3

    device, store = open_device(tmp_path)
    try:
        verdict = device.ask("seal state", subject=SUBJECT)
        assert verdict.kind.value == "ANSWERED_LOCALLY"
        assert {c.claim_id for c in verdict.claims} == set(killed_device)
    finally:
        store.destroy()


def test_nothing_recorded_before_the_kill_reached_the_depot(
    tmp_path, killed_device, depot
):
    """Establishing the negative is what gives the next test its meaning.

    The claims reach the depot because a reconnect delivered them, not because
    something backgrounded them on the way in.
    """
    assert killed_device
    verdict = depot.memory.ask("seal state", subject=SUBJECT)
    assert verdict.kind.value == "UNRESOLVED_CLOUD_REQUIRED"
    assert not verdict.claims
    assert depot.memory.stats()["claims"] == 0


def test_the_restarted_device_delivers_the_days_work_on_reconnect(
    tmp_path, killed_device, depot, link_factory
):
    device, store = open_device(tmp_path)
    try:
        report = link_factory(device).sync()

        assert report.pushed == 3
        assert report.accepted == 3
        assert report.duplicates == 0
        assert report.complete
        assert sorted(c.claim_id for c in depot.memory.memory()) == sorted(
            killed_device
        ), "the depot did not receive exactly the day's work"
    finally:
        store.destroy()


def test_the_queue_drains_once_and_a_second_attempt_sends_nothing(
    tmp_path, killed_device, depot, link_factory
):
    """A drained queue is empty, so a second attempt costs bytes and delivers none."""
    device, store = open_device(tmp_path)
    try:
        link = link_factory(device)
        first = link.sync()
        second = link.sync()

        assert (first.pushed, first.accepted) == (3, 3)
        assert (second.pushed, second.accepted, second.duplicates) == (0, 0, 0)
        assert second.complete
        assert depot.memory.stats()["claims"] == 3, "the replay duplicated a claim"
    finally:
        store.destroy()


# --------------------------------------------------------------------------
# the same guarantee without a kill
# --------------------------------------------------------------------------


def test_a_device_rebuilt_from_its_own_state_answers_the_same(tmp_path):
    """Kept alongside the kill tests because it isolates the queue's part.

    Everything else here could be satisfied by making the shard durable directly.
    This asks the same question of the same device before and after it is thrown
    away and rebuilt out of what is on disk.
    """
    first, first_store = open_device(tmp_path)
    try:
        first.record(claim(value="broken"))
    finally:
        first_store.destroy()

    second, second_store = open_device(tmp_path)
    try:
        verdict = second.ask("seal state", subject=SUBJECT)
        assert verdict.kind.value == "ANSWERED_LOCALLY"
        assert "broken" in verdict.summary
    finally:
        second_store.destroy()


def test_a_claim_the_policy_sealed_never_leaves_the_device(
    tmp_path, depot, link_factory
):
    """The sensitivity veto is a veto on transmission, not a soft preference.

    A claim naming a customer must not leave on the strength of being urgent, and
    the only way to know it did not leave is to ask the central system.
    """
    device, store = open_device(tmp_path)
    try:
        sealed = claim(
            subject="reefer probe",
            attribute="reading",
            value="customer Halvorsen notified",
            sensitivity=0.95,
        )
        device.record(sealed)
        device.record(claim(value="broken"))

        report = link_factory(device).sync()

        assert report.pushed == 1, "a sealed claim left the device"
        assert report.withheld == 1, "the device did not report what it held back"
        assert [c.claim_id for c in depot.memory.memory()] == [
            c.claim_id for c in device.memory() if c.claim_id != sealed.claim_id
        ]

        held = device.ask("reading", subject="reefer probe")
        assert held.kind.value == "ANSWERED_LOCALLY", (
            "a claim that never left must still be answerable from memory"
        )
    finally:
        store.destroy()


def test_a_claim_written_twice_is_queued_once(tmp_path, depot, link_factory):
    """The queue is keyed on the claim id, so re-recording an observation adds nothing."""
    device, store = open_device(tmp_path)
    try:
        recorded = device.record(claim())
        device.record(claim(claim_id=recorded.claim_id, value="broken again"))

        report = link_factory(device).sync()

        assert report.pushed == 1
        assert depot.memory.stats()["claims"] == 1
    finally:
        store.destroy()
