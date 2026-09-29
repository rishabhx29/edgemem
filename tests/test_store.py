"""Store behaviour, observed through its own public surface.

No assertions about index internals or the library's own behaviour — only what
a caller of the store can see.
"""

from __future__ import annotations

import pytest

from edgemem.domain import AuthorityClass, CausalContext, Claim, Trust, utcnow
from edgemem.store import ShardStore


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


def test_claim_round_trips_through_the_store(store):
    original = claim()
    store.upsert([original])
    found = store.get(original.claim_id)
    assert found is not None
    assert found.value == "broken"
    assert found.subject == "trailer T-114 seal"
    assert found.observed_at == original.observed_at


def test_supersession_records_both_claims_rather_than_replacing(store):
    """A revision is a new claim that points at the old one.

    Supersession is recorded, not performed: the earlier claim stays readable
    so that what was previously recorded is never obscured.
    """
    first = claim(value="intact")
    store.upsert([first])
    revised = claim(value="broken", supersedes=first.claim_id)
    store.upsert([revised])

    rows = store.claims_for("trailer T-114 seal")
    assert len(rows) == 2, "both the original and the revision must survive"
    values = {r.value for r in rows}
    assert values == {"intact", "broken"}

    # The link is one-directional and the original is untouched.
    assert store.get(first.claim_id).value == "intact"
    assert store.get(revised.claim_id).supersedes == first.claim_id


def test_re_upserting_the_same_claim_id_updates_in_place(store):
    """The same claim written twice is one row, not two."""
    original = claim(value="intact")
    store.upsert([original])
    store.upsert([claim(claim_id=original.claim_id, value="broken")])
    rows = store.claims_for("trailer T-114 seal")
    assert len(rows) == 1
    assert rows[0].value == "broken"


def test_keyword_search_finds_an_exact_term(store):
    store.upsert(
        [
            claim(value="broken"),
            claim(
                subject="reefer probe",
                attribute="temperature",
                value="minus eighteen degrees",
            ),
        ]
    )
    found, _ = store.search("seal broken", use_dense=False, use_keyword=True)
    assert found
    assert "seal" in found[0][0].text_for_matching()


def test_hybrid_search_uses_both_paths(store):
    store.upsert([claim(value="broken")])
    found, _ = store.search("what state is the seal in", limit=3)
    assert found, "hybrid retrieval returned nothing"
    assert all(path == "hybrid" for _, _, path in found)


def test_retrieval_reports_a_latency(store):
    store.upsert([claim()])
    _, elapsed_ms = store.search("seal", limit=1)
    assert elapsed_ms >= 0.0


def test_index_state_is_reported_honestly(store):
    store.upsert([claim(value="broken"), claim(value="intact")])
    state = store.index_state()
    assert state.points == 2
    described = state.describe()
    assert "indexed" in described or "scanning" in described


def test_optimize_does_not_change_what_is_retrievable(store):
    store.upsert([claim(value="broken")])
    before, _ = store.search("seal broken", use_dense=False, use_keyword=True)
    store.optimize()
    after, _ = store.search("seal broken", use_dense=False, use_keyword=True)
    assert len(before) == len(after)
    assert before[0][0].claim_id == after[0][0].claim_id


def test_quarantined_claim_is_readable_with_its_reason(store):
    held = claim().with_trust(Trust.QUARANTINED, "source class RUMOUR")
    store.upsert([held])
    found = store.get(held.claim_id)
    assert found is not None
    assert found.is_quarantined()
    assert found.quarantine_reason == "source class RUMOUR"


def test_instrument_source_class_survives(store):
    c = claim(source_class=AuthorityClass.INSTRUMENT, causal=CausalContext({"A": 1}))
    store.upsert([c])
    assert store.get(c.claim_id).source_class is AuthorityClass.INSTRUMENT


def test_claims_survive_reopen(store, tmp_path):
    original = claim(value="broken")
    store.upsert([original])
    store.flush()
    store.reopen()
    assert store.get(original.claim_id) is not None


def test_claims_for_filters_by_subject(store):
    store.upsert(
        [
            claim(),
            claim(subject="reefer probe", attribute="temperature", value="cold"),
        ]
    )
    rows = store.claims_for("trailer T-114 seal")
    assert len(rows) == 1
    assert rows[0].subject == "trailer T-114 seal"


def test_delete_removes_a_claim(store):
    c = claim()
    store.upsert([c])
    assert store.get(c.claim_id) is not None
    store.delete([c.claim_id])
    assert store.get(c.claim_id) is None


def test_point_ids_are_stable_for_the_same_claim_id():
    a = ShardStore._point_id("abcdef0123456789")
    b = ShardStore._point_id("abcdef0123456789")
    assert a == b


def test_using_a_closed_store_is_an_error(tmp_path):
    s = ShardStore(tmp_path / "shut", device_id="TRK-7")
    with pytest.raises(RuntimeError):
        s.get("nothing")
    s.open()
    s.close()
    with pytest.raises(RuntimeError):
        s.get("nothing")
