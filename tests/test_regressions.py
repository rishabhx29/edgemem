"""Regressions for the defects the audit found.

Each test here fails against the code as it was when the defect was found. If
one of these passes against a broken implementation, the test is not pulling
its weight.
"""

from __future__ import annotations

import pytest

from edgemem.domain import (
    AuthorityClass,
    Claim,
    Trust,
    utcnow,
)
from edgemem.store import PointIdCollision, ShardStore


@pytest.fixture
def store(tmp_path):
    s = ShardStore(tmp_path / "mutable", device_id="TRK-7")
    s.open()
    yield s
    s.close()
    s.destroy()


def claim(**over) -> Claim:
    now = utcnow()
    base = {
        "subject": "trailer T-114 seal",
        "attribute": "state",
        "value": "broken",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": "TRK-7",
        "observed_at": now,
        "recorded_at": now,
    }
    base.update(over)
    return Claim(**base)


# --------------------------------------------------------------------------
# C1: a claim's own id survives every read path
# --------------------------------------------------------------------------


def test_every_read_path_returns_the_real_claim_id(store):
    """A citation is only worth anything if it identifies the claim."""
    original = claim(value="broken")
    store.upsert([original])

    from_all = store.all_claims()[0]
    from_subject = store.claims_for("trailer T-114 seal")[0]
    from_search = store.search("seal broken", limit=1)[0][0][0]

    for found in (from_all, from_subject, from_search):
        assert found.claim_id == original.claim_id


def test_a_search_result_can_be_refetched_by_its_own_id(store):
    """The defect that made search results unresolvable."""
    store.upsert([claim(value="broken")])
    found = store.search("seal broken", limit=1)[0][0][0]
    assert store.get(found.claim_id) is not None
    assert store.get(found.claim_id).value == "broken"


def test_both_sides_of_a_supersession_are_individually_addressable(store):
    first = claim(value="intact")
    store.upsert([first])
    revised = claim(value="broken", supersedes=first.claim_id)
    store.upsert([revised])

    for row in store.claims_for("trailer T-114 seal"):
        assert store.get(row.claim_id) is not None, (
            f"row {row.claim_id} could not be refetched by its own id"
        )
    assert store.get(first.claim_id).value == "intact"
    assert store.get(revised.claim_id).supersedes == first.claim_id


def test_claim_id_travels_in_the_payload(store):
    original = claim()
    store.upsert([original])
    assert "claim_id" in original.to_payload()
    assert original.to_payload()["claim_id"] == original.claim_id


# --------------------------------------------------------------------------
# C2: a point-id collision must never destroy a claim
# --------------------------------------------------------------------------


def test_point_ids_are_injective_for_structured_ids():
    """The defect: ids sharing a 15-hex prefix folded onto one point."""
    shared = "a" * 15
    ids = [f"{shared}{suffix:017x}" for suffix in range(500)]
    mapped = {ShardStore._point_id(i) for i in ids}
    assert len(mapped) == len(ids), "structured claim ids collided"


def test_point_ids_are_total_over_arbitrary_input():
    """A depot-supplied id that is not hex must not raise."""
    for odd in (
        "00000000-0000-0000-0000-000000000001",
        "not-a-uuid-at-all",
        "",
        "  spaced  ",
        "0x1234",
        "1_000_000",
    ):
        assert isinstance(ShardStore._point_id(odd), int)


def test_point_ids_use_the_full_digest_width():
    mapped = {ShardStore._point_id(f"claim-{i}") for i in range(20_000)}
    assert len(mapped) == 20_000


def test_upsert_refuses_to_destroy_a_foreign_claim(store, monkeypatch):
    """Simulate a digest collision: two ids, one point.

    The store must raise rather than let one claim overwrite the other.
    """
    victim = claim(value="intact", claim_id="a" * 32)
    store.upsert([victim])

    # Force the collision deterministically: every id maps to the victim's point.
    real = ShardStore._point_id
    monkeypatch.setattr(
        ShardStore, "_point_id", staticmethod(lambda cid: real(victim.claim_id))
    )
    intruder = claim(value="broken", claim_id="b" * 32)

    with pytest.raises(PointIdCollision):
        store.upsert([intruder])

    assert store.get(victim.claim_id).value == "intact", (
        "the original claim was destroyed by a colliding upsert"
    )


# --------------------------------------------------------------------------
# H4: enums are coerced, and a bare string is not trusted
# --------------------------------------------------------------------------


def test_a_bare_string_trust_is_coerced():
    c = claim(trust="QUARANTINED")
    assert c.trust is Trust.QUARANTINED
    assert c.is_quarantined()


def test_a_bare_string_source_class_is_coerced():
    c = claim(source_class="INSTRUMENT")
    assert c.source_class is AuthorityClass.INSTRUMENT
    assert c.to_payload()["source_class"] == "INSTRUMENT"


def test_out_of_range_sensitivity_is_rejected():
    for bad in (99.0, -1.0, 1.5):
        with pytest.raises(ValueError, match="sensitivity"):
            claim(sensitivity=bad)


def test_an_invalid_enum_names_the_offending_value():
    with pytest.raises(ValueError, match="MAYBE"):
        claim(trust="MAYBE")


# --------------------------------------------------------------------------
# M1: scroll pagination must not silently truncate
# --------------------------------------------------------------------------


def test_all_claims_does_not_truncate_at_the_page_size(store):
    total = 250
    store.upsert(
        [
            claim(
                claim_id=f"{i:032x}",
                subject="bulk subject",
                attribute="reading",
                value=f"reading {i}",
            )
            for i in range(total)
        ]
    )
    assert len(store.all_claims()) == total
    assert len(store.claims_for("bulk subject")) == total


# --------------------------------------------------------------------------
# M2: the index report must not overstate
# --------------------------------------------------------------------------


def test_index_report_does_not_exceed_the_claim_count(store):
    store.upsert([claim()])
    store.optimize()
    state = store.index_state()
    described = state.describe()
    assert str(state.points) in described
    # The indexed-vector count may legitimately exceed the point count after an
    # optimize, so it must not be what the report quotes.
    assert f"indexed ({state.indexed_vectors} vectors)" not in described


def test_index_coverage_never_exceeds_one(store):
    store.upsert([claim()])
    store.optimize()
    assert store.index_state().coverage <= 1.0
