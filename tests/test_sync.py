"""Incremental sync, in both directions, over a real socket.

Two devices and a genuine loopback listener between them. Nothing here is mocked:
the depot is a second ``EdgeMemory`` behind an ``http.server`` handler, and a claim
that goes up comes back down because it crossed a socket.

The observations are ``SyncReport`` fields and what ``ask`` returns afterwards.
The report is an accounting surface rather than a second seam: it says what moved,
and every claim it mentions is then checked by asking a question about it.

The active sync path is asserted rather than assumed. No Qdrant server can run on
this machine, so the vendor's shard-manifest path cannot run either, and every
report in this file says ``depot_delta`` because that is what executed.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.request

import pytest

from edgemem.domain import AuthorityClass, Claim, utcnow
from edgemem.memory import EdgeMemory
from edgemem.outbox import Outbox
from edgemem.store import ShardStore
from edgemem.sync import (
    Depot,
    DeviceLink,
    ProtocolError,
    SyncPath,
    VendorSnapshotUnavailable,
)

SEAL = "trailer T-114 seal"
PROBE = "reefer probe"


def claim(**over) -> Claim:
    now = utcnow()
    base = {
        "subject": SEAL,
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


def reading(device_id: str, value: str) -> Claim:
    """A claim one device made, causally after nothing else."""
    made = claim(
        subject=PROBE,
        attribute="temperature",
        value=value,
        author=f"operator-{device_id[-1]}",
        device_id=device_id,
    )
    made.causal.observe(device_id)
    return made


def closed_port() -> str:
    """An address on this machine that nothing is listening on."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------
# fixtures: two devices and a depot, all real
# --------------------------------------------------------------------------


@pytest.fixture
def depot_factory(tmp_path):
    built: list[Depot] = []

    def make(depot_id: str = "DEPOT-1") -> Depot:
        store = ShardStore(tmp_path / f"shard-{depot_id}", device_id=depot_id)
        store.open()
        central = Depot(EdgeMemory(store), tmp_path / "depot-ledger.jsonl", depot_id)
        built.append(central)
        return central

    yield make
    for central in built:
        central.stop()
        central.memory.store.destroy()


@pytest.fixture
def depot(depot_factory):
    central = depot_factory()
    central.serve()
    return central


@pytest.fixture
def device_factory(tmp_path):
    opened: list[ShardStore] = []

    def make(device_id: str = "TRK-7") -> EdgeMemory:
        store = ShardStore(tmp_path / f"shard-{device_id}", device_id=device_id)
        store.open()
        opened.append(store)
        return EdgeMemory(store, outbox=Outbox(tmp_path / f"outbox-{device_id}.jsonl"))

    yield make
    for store in opened:
        store.destroy()


@pytest.fixture
def link_factory(tmp_path):
    def build(device: EdgeMemory, central: Depot, name: str = "cursor", url: str | None = None):
        return DeviceLink(
            device,
            device.outbox,
            url or central.url,
            tmp_path / f"{name}-{device.device_id}.json",
        )

    return build


# --------------------------------------------------------------------------
# the ticket's own acceptance test
# --------------------------------------------------------------------------


def test_a_claim_recorded_on_one_device_is_present_on_the_other(
    device_factory, depot, link_factory
):
    """Both ends must account for the same transfer, or neither is measuring it."""
    device = device_factory("TRK-7")
    recorded = device.record(claim(value="broken"))

    report = link_factory(device, depot).sync()

    assert report.path == SyncPath.DEPOT_DELTA.value
    assert report.pushed == 1
    assert report.accepted == 1
    assert report.bytes_sent > 0, "the device reported no cost for a transfer"
    assert report.bytes_received > 0

    arrived = depot.memory.ask("seal state", subject=SEAL)
    assert arrived.kind.value == "ANSWERED_LOCALLY"
    assert [c.claim_id for c in arrived.claims] == [recorded.claim_id]

    central = depot.status()
    assert central["path"] == SyncPath.DEPOT_DELTA.value
    assert central["bytes_received"] > 0
    assert central["bytes_sent"] > 0


def test_both_ends_measure_the_same_bytes_and_the_report_is_plain_data(
    device_factory, depot, link_factory
):
    """The device's figure for what it sent and the depot's for what it read are
    two measurements of one transfer, so they must agree exactly. A modelled
    figure on either side would break that, which is why it is asserted."""
    device = device_factory("TRK-7")
    device.record_many([claim(value="intact"), claim(value="broken")])

    report = link_factory(device, depot).sync()

    assert report.bytes_sent == report.depot_bytes_received
    assert json.loads(json.dumps(report.to_payload()))["path"] == "depot_delta"
    assert report.duration_ms > 0.0
    assert report.describe()


# --------------------------------------------------------------------------
# only what changed crosses the link
# --------------------------------------------------------------------------


def test_a_reconnect_transfers_only_what_changed(
    device_factory, depot, link_factory
):
    """Two claims go up, then one comes down, and the device's own never come back."""
    uplink = device_factory("TRK-7")
    uplink.record_many([claim(value="intact"), claim(value="broken")])

    link = link_factory(uplink, depot, "a")
    link.sync()
    assert link.sync().pulled == 0, (
        "the depot sent the device back a claim the device had just given it"
    )

    downlink = device_factory("TRK-9")
    downlink.record(reading("TRK-9", "minus eighteen degrees"))
    link_factory(downlink, depot, "b").sync()

    arrived = link.sync()
    assert arrived.pulled == 1
    assert arrived.pushed == 0

    settled = uplink.ask("temperature", subject=PROBE)
    assert settled.kind.value == "ANSWERED_LOCALLY"
    assert settled.claims[0].device_id == "TRK-9"


def test_a_reconnect_with_nothing_to_do_costs_a_fraction_of_the_first(
    device_factory, depot, link_factory
):
    """The incrementality as a number rather than a claim: an idle device's
    reconnect must be materially cheaper than the one that moved the day's work."""
    device = device_factory("TRK-7")
    device.record_many([claim(value=v) for v in ("intact", "broken", "resecured")])
    link = link_factory(device, depot, "a")

    first = link.sync()
    second = link.sync()

    assert first.pushed == 3 and first.bytes_sent > 0
    assert (second.pushed, second.pulled) == (0, 0)
    assert second.bytes_sent > 0, "a sync with nothing to do still asked the question"
    assert second.bytes_sent < first.bytes_sent / 2, (
        f"an idle reconnect cost {second.bytes_sent} against {first.bytes_sent}"
    )


def test_a_device_does_not_receive_back_the_claim_it_just_sent(
    device_factory, depot, link_factory
):
    device = device_factory("TRK-7")
    device.record(claim())

    report = link_factory(device, depot).sync()

    assert (report.pushed, report.accepted, report.pulled) == (1, 1, 0)
    assert report.cursor_after == report.cursor_before + 1


# --------------------------------------------------------------------------
# interruption costs nothing
# --------------------------------------------------------------------------


def test_an_interrupted_pull_resumes_without_losing_or_duplicating(
    device_factory, depot, link_factory
):
    """The pull is paged and the retained position is written after each page, so
    stopping between pages costs a round trip and not a claim."""
    uplink = device_factory("TRK-7")
    uplink.record_many([claim(value=v) for v in ("intact", "broken", "resecured")])
    link_factory(uplink, depot, "seed").sync()

    downlink = device_factory("TRK-9")
    link = link_factory(downlink, depot, "down")
    landed: list[int] = []

    def stop_after_the_first_page(received: int) -> None:
        landed.append(received)
        raise RuntimeError("the uplink dropped")

    with pytest.raises(RuntimeError, match="uplink dropped"):
        link.sync(page_size=1, on_page=stop_after_the_first_page)

    assert landed == [1], "the interrupted page was never applied"

    resumed = link.sync()
    assert resumed.pulled == 2
    assert resumed.complete
    assert downlink.stats()["claims"] == 3, "the resume lost or duplicated a claim"
    assert [c.claim_id for c in downlink.memory()] == [
        c.claim_id for c in uplink.memory()
    ]


def test_a_dropped_link_loses_nothing_and_the_next_attempt_sends_everything(
    device_factory, depot, link_factory
):
    """The uplink is severed before the first attempt, so nothing has been
    acknowledged and nothing may be treated as though it had been."""
    device = device_factory("TRK-7")
    recorded = [device.record(claim(value=v)).claim_id for v in ("intact", "broken")]

    with pytest.raises(ProtocolError):
        link_factory(device, depot, "severed", url=closed_port()).sync()

    offline = device.ask("seal state", subject=SEAL)
    assert offline.kind.value == "ANSWERED_LOCALLY", (
        "the device stopped answering with the link down"
    )

    report = link_factory(device, depot, "restored").sync()
    assert report.pushed == len(recorded)
    assert report.accepted == len(recorded)
    assert sorted(c.claim_id for c in depot.memory.memory()) == sorted(recorded)


def test_a_claim_written_while_a_pull_is_in_flight_is_applied_not_clobbered(
    device_factory, depot, link_factory
):
    """Local work lands beside the incoming page and survives it."""
    uplink = device_factory("TRK-7")
    uplink.record_many([claim(value=v) for v in ("intact", "broken", "resecured")])
    link_factory(uplink, depot, "seed").sync()

    downlink = device_factory("TRK-9")
    link = link_factory(downlink, depot, "down")
    local = reading("TRK-9", "minus eighteen degrees")
    interleaved: list[int] = []

    def record_after_the_first_page(received: int) -> None:
        if received and not interleaved:
            interleaved.append(received)
            downlink.record(local)

    report = link.sync(page_size=1, on_page=record_after_the_first_page)

    assert interleaved == [1]
    assert report.held_during_apply == 1, (
        "the sync did not report the local write that happened under it"
    )
    assert report.pulled == 3
    assert local.claim_id in {c.claim_id for c in downlink.memory()}
    assert downlink.ask("temperature", subject=PROBE).kind.value == "ANSWERED_LOCALLY"
    assert downlink.ask("seal state", subject=SEAL).kind.value == "ANSWERED_LOCALLY"


def test_a_write_racing_a_running_sync_is_never_lost(
    device_factory, depot, link_factory
):
    """A claim written from another thread while the exchange is in flight.

    Only the invariant is asserted, not the interleaving: whether the write lands
    before the first page or after the last, it must be there afterwards. A test
    that pinned the order would be testing the scheduler.
    """
    uplink = device_factory("TRK-7")
    uplink.record_many([claim(value=v) for v in ("intact", "broken", "resecured")])
    link_factory(uplink, depot, "seed").sync()

    downlink = device_factory("TRK-9")
    link = link_factory(downlink, depot, "down")
    in_flight = threading.Event()
    finished = threading.Event()
    late = reading("TRK-9", "plus four degrees")

    def write_while_syncing() -> None:
        in_flight.wait(timeout=30)
        try:
            downlink.record(late)
        finally:
            finished.set()

    writer = threading.Thread(target=write_while_syncing, daemon=True)
    writer.start()
    report = link.sync(page_size=1, on_page=lambda received: in_flight.set())
    assert finished.wait(timeout=30), "the concurrent write never completed"
    writer.join(timeout=30)

    assert report.pulled == 3
    held = {c.claim_id for c in downlink.memory()}
    assert late.claim_id in held
    assert {c.claim_id for c in uplink.memory()} <= held


def test_the_incoming_page_does_not_overwrite_what_the_device_already_holds(
    device_factory, depot, link_factory
):
    """A claim is an attributed assertion, so an arriving copy under the same
    identity is reported rather than applied. The device keeps what it observed."""
    witness = device_factory("TRK-7")
    original = witness.record(claim(value="broken"))
    link_factory(witness, depot, "seed").sync()

    other = device_factory("TRK-9")
    # The substrate is arranged directly here so the depot holds a copy that
    # disagrees with what the device observed. Everything after that is asked
    # through the seam.
    other.store.upsert(
        [claim(claim_id=original.claim_id, value="intact", device_id="TRK-9")]
    )

    report = link_factory(other, depot, "other").sync()

    assert report.pulled == 1
    assert report.reapplied == 1, "the device did not report the disagreement"
    assert any(original.claim_id in note for note in report.notes)
    answer = other.ask("seal state", subject=SEAL)
    assert "intact" in answer.summary, "the depot's copy overwrote the device's own"


def test_a_pull_never_removes_a_claim_to_reclaim_space(
    device_factory, depot, link_factory
):
    """Sync annotates and merges. Reclaiming space is a separate, explicit act."""
    uplink = device_factory("TRK-7")
    uplink.record_many([claim(value=v) for v in ("intact", "broken", "resecured")])
    link_factory(uplink, depot, "seed").sync()

    downlink = device_factory("TRK-9")
    sealed = [
        downlink.record(
            claim(
                subject=PROBE,
                attribute="reading",
                value=f"customer {n} notified",
                sensitivity=0.95,
            )
        )
        for n in range(3)
    ]

    report = link_factory(downlink, depot, "down").sync()

    assert report.withheld == 3
    assert report.pulled == 3
    assert downlink.stats()["claims"] == 6
    held = {c.claim_id for c in downlink.memory()}
    assert {c.claim_id for c in sealed} <= held
    assert {c.claim_id for c in uplink.memory()} <= held
    assert any("not yet at the depot" in note for note in report.notes)


# --------------------------------------------------------------------------
# identity survives the journey
# --------------------------------------------------------------------------


def test_an_absorbed_claim_keeps_its_identity_and_its_causality(
    device_factory, depot, link_factory
):
    """The causality conflict detection depends on is the point of this test.

    An incoming claim re-stamped as this device's own observation would stop being
    concurrent with the local one, and the disagreement would disappear: a device
    that learned the other side of a conflict would silently lose it.
    """
    witness = device_factory("TRK-7")
    dock = claim(
        value="intact",
        observer="dock-scanner",
        source_class=AuthorityClass.INSTRUMENT,
        device_id="DEPOT-9",
    )
    dock.causal.observe("DEPOT-9")
    witness.record(dock)
    link_factory(witness, depot, "seed").sync()

    other = device_factory("TRK-9")
    link_factory(other, depot, "down").sync()

    learned = other.memory()
    assert [c.claim_id for c in learned] == [dock.claim_id]
    assert learned[0].author == dock.author
    assert learned[0].device_id == "DEPOT-9"
    assert learned[0].observed_at == dock.observed_at
    assert learned[0].causal.to_payload() == dock.causal.to_payload()

    local = claim(value="broken", device_id="TRK-9")
    local.causal.observe("TRK-9")
    other.record(local)

    verdict = other.ask("seal state", subject=SEAL)
    assert verdict.kind.value == "CONFLICTED", (
        "the device lost the disagreement by absorbing the other side of it"
    )
    a, b = verdict.conflicts[0]
    assert {a.claim.value, b.claim.value} == {"intact", "broken"}
    assert {a.claim.device_id, b.claim.device_id} == {"DEPOT-9", "TRK-9"}
    assert dock.claim_id in {a.claim.claim_id, b.claim.claim_id}
    assert {a.claim.observed_at, b.claim.observed_at} == {
        dock.observed_at.isoformat(),
        local.observed_at.isoformat(),
    }


# --------------------------------------------------------------------------
# at-least-once delivery
# --------------------------------------------------------------------------


def test_a_claim_the_depot_already_holds_is_a_duplicate_and_is_not_stored_twice(
    device_factory, depot, link_factory
):
    """The replay half of at-least-once.

    A queue re-delivered after an interrupted acknowledgement presents the same
    claim id again. The depot must recognise it rather than store a second copy,
    and the device that holds the differing observation keeps it.
    """
    first = device_factory("TRK-7")
    original = first.record(claim(value="broken"))
    link_factory(first, depot, "seed").sync()

    second = device_factory("TRK-9")
    second.record(
        claim(claim_id=original.claim_id, value="intact", device_id="TRK-9")
    )
    report = link_factory(second, depot, "second").sync()

    assert report.pushed == 1
    assert report.accepted == 0
    assert report.duplicates == 1, "the depot did not recognise a claim it held"
    assert depot.memory.stats()["claims"] == 1
    assert depot.memory.ask("seal state", subject=SEAL).claims[0].value == "broken"

    # The second device keeps what it saw. Disagreement is not a version.
    assert second.ask("seal state", subject=SEAL).claims[0].value == "intact"


# --------------------------------------------------------------------------
# the active path is reported, not assumed
# --------------------------------------------------------------------------


def test_both_ends_report_the_path_that_actually_ran(
    device_factory, depot, link_factory
):
    device = device_factory("TRK-7")
    device.record(claim())

    report = link_factory(device, depot).sync()

    assert report.path == "depot_delta"
    assert report.depot_path == "depot_delta"
    assert depot.status()["path"] == "depot_delta"

    with urllib.request.urlopen(f"{depot.url}/status", timeout=10) as response:
        over_the_wire = json.loads(response.read())
    assert over_the_wire["path"] == "depot_delta"
    assert over_the_wire["claims"] == 1


def test_the_vendor_path_is_refused_rather_than_pretended(
    tmp_path, device_factory
):
    """No Qdrant server can run here, so the vendor path cannot run either.

    Reporting ``depot_delta`` because that is what ran is the requirement. Running
    something else under the vendor's name would be the failure this repository
    exists to prevent.
    """
    store = ShardStore(tmp_path / "alt", device_id="DEPOT-2")
    store.open()
    try:
        with pytest.raises(VendorSnapshotUnavailable, match="Qdrant server"):
            Depot(
                EdgeMemory(store),
                tmp_path / "alt-ledger.jsonl",
                depot_id="DEPOT-2",
                sync_path=SyncPath.VENDOR_SNAPSHOT,
            )
        with pytest.raises(VendorSnapshotUnavailable, match="Qdrant server"):
            DeviceLink(
                device_factory("TRK-7"),
                Outbox(tmp_path / "alt-outbox.jsonl"),
                closed_port(),
                tmp_path / "alt-cursor.json",
                path=SyncPath.VENDOR_SNAPSHOT,
            )
    finally:
        store.close()


# --------------------------------------------------------------------------
# the change order is monotonic and durable
# --------------------------------------------------------------------------


def test_the_depot_keeps_numbering_across_a_restart(
    tmp_path, depot_factory, device_factory
):
    """A device's retained position only means something if the sequence it refers
    to means the same thing after the depot is rebuilt. A depot that renumbered
    would leave every device waiting on a sequence that never arrives."""
    central = depot_factory()
    central.serve()
    first = device_factory("TRK-7")
    first.record_many([claim(value=v) for v in ("intact", "broken")])
    DeviceLink(
        first, first.outbox, central.url, tmp_path / "seed-cursor.json"
    ).sync()

    central.stop()
    central.memory.store.close()

    rebuilt = depot_factory()
    assert rebuilt.status()["numbered"] == 2, "the change order did not survive"
    assert rebuilt.status()["next_seq"] == 3
    rebuilt.serve()

    late = device_factory("TRK-11")
    late.record(reading("TRK-11", "minus four degrees"))
    report = DeviceLink(
        late, late.outbox, rebuilt.url, tmp_path / "late-cursor.json"
    ).sync()

    assert report.accepted == 1
    assert report.pulled == 2, "the rebuilt depot did not serve its own history"
    assert report.cursor_after == 3, (
        "the sequence restarted, or the device's retained position is wrong"
    )
    assert [c.device_id for c in late.memory() if c.subject == SEAL] == [
        "TRK-7",
        "TRK-7",
    ]
