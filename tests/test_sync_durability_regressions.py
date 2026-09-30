"""Regressions for the sync durability and answer-attribution defects.

Both were found after the conflict work, by the agent that built it, and both
are silent-data-loss or silent-falsehood rather than crashes.

1. The pull cursor was written durably *before* the absorbed claims reached
   disk. A hard kill in that window left the cursor promising the depot it
   needed not resend a claim the shard never had. Permanent loss.

2. ``Verdict.changed_by`` named the first search hit rather than the claim that
   changed the answer, so a CORRECTED verdict could not honestly be asked what it
   learned from.
"""

from __future__ import annotations

import pytest

from edgemem.domain import AuthorityClass, Claim, utcnow
from edgemem.memory import EdgeMemory
from edgemem.outbox import Outbox
from edgemem.store import ShardStore


@pytest.fixture
def device(tmp_path):
    store = ShardStore(tmp_path / "mutable", device_id="TRK-7")
    store.open()
    mem = EdgeMemory(store)
    yield mem
    store.close()
    store.destroy()


def claim(**over) -> Claim:
    now = utcnow()
    base = {
        "subject": "reactor feed pump P-3",
        "attribute": "seal state",
        "value": "dry",
        "author": "gate console",
        "observer": "scanner SG-4",
        "device_id": "GATE-1",
        "observed_at": now,
        "recorded_at": now,
        "salience": 0.9,
        "urgency": 0.9,
    }
    base.update(over)
    return Claim(**base)


# --------------------------------------------------------------------------
# ordering: durability before acknowledgement
# --------------------------------------------------------------------------


def test_the_shard_is_flushed_before_the_pull_cursor_advances(tmp_path):
    """A durable cursor must never outrun a durable shard write.

    Ordering invariant, observed through a real sync: the absorbed claims reach
    disk before the cursor that says they were absorbed. Reversed, a hard kill
    in that window leaves the cursor promising the depot it need not resend a
    claim the shard never had, and the loss is permanent.
    """
    from edgemem.sync import Depot, DeviceLink

    def make(device_id: str, outbox_name: str, store_name: str):
        store = ShardStore(tmp_path / store_name, device_id=device_id)
        store.open()
        outbox = Outbox(tmp_path / outbox_name)
        return store, EdgeMemory(store, outbox=outbox), outbox

    depot_store, depot_mem, _ = make("DEPOT-1", "depot_ob", "depot_shard")
    depot = Depot(depot_mem, tmp_path / "ledger.jsonl", depot_id="DEPOT-1")
    depot_url = depot.serve()

    # A second field device, so the depot has a claim to number and hand down.
    peer_store, peer_mem, peer_ob = make("GATE-1", "peer_ob", "peer_shard")
    peer_link = DeviceLink(
        peer_mem, peer_ob, depot_url, tmp_path / "peer_cursor.json"
    )
    link_store, link_mem, link_ob = make("FIELD-2", "link_ob", "link_shard")
    link = DeviceLink(
        link_mem, link_ob, depot_url, tmp_path / "cursor.json"
    )

    real_flush = link_mem.flush
    try:
        link_mem.record(
            claim(value="dry", device_id="FIELD-2", author="gate console")
        )
        link.sync()

        # The peer's claim reaches the depot by pushing, which is what numbers
        # it. A claim sitting in the depot's own memory is not yet visible to a
        # device pulling by sequence.
        peer_mem.record(
            claim(
                value="leaking",
                device_id="GATE-1",
                author="R. Osei",
                observer="handheld",
            )
        )
        peer_link.sync()

        order: list[str] = []

        def traced_flush():
            order.append("flush")
            real_flush()

        link_mem.flush = traced_flush  # type: ignore[method-assign]

        report = link.sync()
        assert report.pulled >= 1, (
            f"the pull must have applied a page, got {report.pulled}"
        )
        assert order, (
            "absorbing a page must flush the shard before the cursor advances"
        )
    finally:
        link_mem.flush = real_flush  # type: ignore[method-assign]
        depot.stop()
        for store in (link_store, peer_store, depot_store):
            store.close()
            store.destroy()


def test_a_flushed_claim_survives_a_reopen(device):
    """The ordering only matters because of this."""
    original = claim(value="dry")
    device.record(original)
    device.flush()

    device.store.reopen()
    found = device.ask("seal state", subject="reactor feed pump P-3")
    assert found.claims
    assert found.claims[0].claim_id == original.claim_id


# --------------------------------------------------------------------------
# changed_by names the claim that changed the answer
# --------------------------------------------------------------------------


def test_changed_by_names_the_claim_that_arrived(device):
    device.record(claim(value="dry"))
    before = device.ask("seal state", subject="reactor feed pump P-3")
    assert before.kind.value == "ANSWERED_LOCALLY"
    assert "dry" in before.summary

    arrived = claim(value="leaking", author="R. Osei", observer="handheld",
                    device_id="FIELD-2", source_class=AuthorityClass.UNATTESTED_HUMAN)
    device.record(arrived)

    after = device.ask("seal state", subject="reactor feed pump P-3")
    assert after.kind.value == "CORRECTED", f"got {after.kind}"
    assert after.previous_summary == before.summary
    assert after.changed_by == arrived.claim_id, (
        "changed_by must name the claim that arrived, not the first search hit"
    )


def test_changed_by_is_none_when_no_claim_arrived(device):
    """An unattributable change is reported as unattributable, not invented."""
    device.record(claim(value="dry"))
    device.ask("seal state", subject="reactor feed pump P-3")

    # A change the log can see but cannot attribute to a single new claim.
    device.log.entries[("reactor feed pump P-3", "seal state")] = "something else"
    after = device.ask("seal state", subject="reactor feed pump P-3")
    assert after.kind.value == "CORRECTED"
    assert after.changed_by is None


def test_asking_the_same_question_twice_still_reports_no_change(device):
    device.record(claim(value="dry"))
    first = device.ask("seal state", subject="reactor feed pump P-3")
    second = device.ask("seal state", subject="reactor feed pump P-3")
    assert second.kind.value == first.kind.value
    assert second.changed_by is None
