"""Ticket 09: the change is visible without the caller asking twice.

The gap this covers: a device that could not answer, then learned enough to
answer, previously reported its first confident answer as ANSWERED_LOCALLY. The
operator had no way to see that the belief had changed, which is the one moment
worth seeing. The verdict now reports the transition itself.
"""

from __future__ import annotations

import pytest

from edgemem.domain import AuthorityClass, Claim, utcnow
from edgemem.memory import EdgeMemory
from edgemem.outbox import Outbox
from edgemem.store import ShardStore
from edgemem.sync import Depot, DeviceLink

SUBJECT = "trailer T-114 seal"
QUESTION = "seal state"


@pytest.fixture
def world(tmp_path):
    """A depot and two devices, over a real socket."""
    shards: list[ShardStore] = []

    def device(device_id: str):
        store = ShardStore(tmp_path / f"{device_id}-shard", device_id=device_id)
        store.open()
        shards.append(store)
        outbox = Outbox(tmp_path / f"{device_id}-outbox.jsonl")
        return EdgeMemory(store, outbox=outbox), outbox

    depot_mem, depot_ob = device("DEPOT-1")
    depot = Depot(depot_mem, tmp_path / "ledger.jsonl", depot_id="DEPOT-1")
    url = depot.serve()

    truck, truck_ob = device("TRK-7")
    gate, gate_ob = device("GATE-1")

    yield {
        "depot": depot,
        "truck": truck,
        "gate": gate,
        "truck_link": DeviceLink(truck, truck_ob, url, tmp_path / "c-truck.json"),
        "gate_link": DeviceLink(gate, gate_ob, url, tmp_path / "c-gate.json"),
        "shards": shards,
    }

    depot.stop()
    for store in shards:
        store.close()
        store.destroy()


def gate_claim(**over) -> Claim:
    now = utcnow()
    base = {
        "subject": SUBJECT,
        "attribute": "state",
        "value": "broken",
        "author": "gate console",
        "observer": "scanner",
        "device_id": "GATE-1",
        "observed_at": now,
        "recorded_at": now,
        "source_class": AuthorityClass.INSTRUMENT,
    }
    base.update(over)
    return Claim(**base)


def test_a_device_that_could_not_answer_reports_the_change_when_it_learns(world):
    truck = world["truck"]

    before = truck.ask(QUESTION, subject=SUBJECT)
    assert before.kind.value == "UNRESOLVED_CLOUD_REQUIRED"
    assert not before.claims, "the device had nothing to answer with"

    arrived = gate_claim()
    world["gate"].record(arrived)
    world["gate_link"].sync()
    world["truck_link"].sync()

    after = truck.ask(QUESTION, subject=SUBJECT)
    assert after.kind.value == "CORRECTED", (
        f"becoming able to answer must be reported, got {after.kind}"
    )
    assert after.changed_by == arrived.claim_id, (
        "the verdict must name the claim that changed the device's belief"
    )


def test_the_change_is_settled_once_nothing_new_is_learned(world):
    truck = world["truck"]
    # Ask first, so there is a prior "could not answer" for the learning to
    # correct. Without that prior, a first confident answer is not news.
    assert truck.ask(QUESTION, subject=SUBJECT).kind.value == (
        "UNRESOLVED_CLOUD_REQUIRED"
    )

    world["gate"].record(gate_claim())
    world["gate_link"].sync()
    world["truck_link"].sync()

    assert truck.ask(QUESTION, subject=SUBJECT).kind.value == "CORRECTED"
    assert truck.ask(QUESTION, subject=SUBJECT).kind.value == "ANSWERED_LOCALLY"


def test_no_correction_when_the_device_could_already_answer(world):
    """Only a change of belief is news. A steady answer stays steady."""
    truck = world["truck"]
    truck.record(
        gate_claim(value="intact", device_id="TRK-7", author="operator-7")
    )
    first = truck.ask(QUESTION, subject=SUBJECT)
    assert first.kind.value == "ANSWERED_LOCALLY"
    second = truck.ask(QUESTION, subject=SUBJECT)
    assert second.kind.value == "ANSWERED_LOCALLY"
    assert second.changed_by is None