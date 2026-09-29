"""Domain-layer behaviour, through its own public surface.

These are pure-object tests: no shard, no network. They exist because the causal
tests are the ones worth being certain about, and they are cheap to run.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from edgemem.domain import (
    AuthorityClass,
    CausalContext,
    Claim,
    Residency,
    ResidencyReason,
    Trust,
    parse_iso,
    utcnow,
)


def make_claim(**over) -> Claim:
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
# causal context
# --------------------------------------------------------------------------


def test_sequential_writes_are_not_concurrent():
    first = CausalContext()
    first.observe("A")
    second = first.copy_for_write("A")
    second.observe("A")

    assert not first.is_concurrent_with(second)
    assert second.dominates(first)
    assert not first.dominates(second)


def test_writes_on_different_devices_are_concurrent():
    # Both devices wrote from the same prior state, neither having seen the
    # other. That is what concurrency means, and it is distinct from one
    # device writing on top of the other's work.
    left = CausalContext()
    left.observe("A")

    right = CausalContext()
    right.observe("B")

    assert left.is_concurrent_with(right)
    assert right.is_concurrent_with(left)
    assert not left.dominates(right)
    assert not right.dominates(left)


def test_a_write_on_top_of_a_peer_is_not_concurrent():
    # B saw A's write (its counter includes A), so A's and B's contexts are
    # causally ordered even though different devices touched the subject.
    left = CausalContext()
    left.observe("A")

    right = left.copy_for_write("B")
    right.observe("B")

    assert not left.is_concurrent_with(right)
    assert right.dominates(left)


def test_identical_contexts_are_not_concurrent():
    ctx = CausalContext({"A": 1, "B": 1})
    assert not ctx.is_concurrent_with(CausalContext({"A": 1, "B": 1}))


def test_merge_takes_per_device_maximum():
    left = CausalContext({"A": 3, "B": 1})
    right = CausalContext({"A": 1, "B": 4})
    assert left.merged_with(right).versions == {"A": 3, "B": 4}


def test_context_size_tracks_devices_not_edits():
    ctx = CausalContext()
    for _ in range(50):
        ctx.observe("A")
    assert len(ctx.versions) == 1

    ctx.observe("B")
    assert len(ctx.versions) == 2


def test_context_survives_payload_round_trip():
    ctx = CausalContext({"A": 2, "B": 5})
    assert CausalContext.from_payload(ctx.to_payload()) == ctx
    assert CausalContext.from_payload(None) == CausalContext()


# --------------------------------------------------------------------------
# claim round trip
# --------------------------------------------------------------------------


def test_claim_survives_payload_round_trip():
    claim = make_claim(
        causal=CausalContext({"TRK-7": 1}),
        source_class=AuthorityClass.INSTRUMENT,
        sensitivity=0.9,
        urgency=0.8,
    )
    restored = Claim.from_payload(claim.claim_id, claim.to_payload())

    assert restored.claim_id == claim.claim_id
    assert restored.subject == claim.subject
    assert restored.value == claim.value
    assert restored.observed_at == claim.observed_at
    assert restored.source_class is AuthorityClass.INSTRUMENT
    assert restored.sensitivity == pytest.approx(0.9)
    assert restored.causal.versions == {"TRK-7": 1}


def test_superseded_claim_remains_readable():
    first = make_claim(value="intact")
    second = make_claim(value="broken", supersedes=first.claim_id)

    restored = Claim.from_payload(second.claim_id, second.to_payload())
    assert restored.supersedes == first.claim_id
    # the earlier claim is untouched and still parses
    assert Claim.from_payload(first.claim_id, first.to_payload()).value == "intact"


def test_quarantine_is_visible_on_the_claim():
    held = make_claim().with_trust(Trust.QUARANTINED, "source class RUMOUR")
    assert held.is_quarantined()
    assert held.to_payload()["quarantine_reason"] == "source class RUMOUR"
    assert Claim.from_payload(held.claim_id, held.to_payload()).is_quarantined()


def test_observed_and_recorded_times_are_independent():
    observed = utcnow()
    recorded = observed + timedelta(hours=6)
    claim = make_claim(observed_at=observed, recorded_at=recorded)
    restored = Claim.from_payload(claim.claim_id, claim.to_payload())
    assert restored.recorded_at - restored.observed_at == timedelta(hours=6)


# --------------------------------------------------------------------------
# residency reason
# --------------------------------------------------------------------------


def test_reason_names_the_signals_that_fired():
    reason = ResidencyReason(
        residency=Residency.LOCAL,
        signals=("sensitivity 0.92 >= 0.80", "urgency 0.10 < 0.50"),
        vetoed_by="sensitivity",
    )
    rendered = reason.render()
    assert "sensitivity 0.92 >= 0.80" in rendered
    assert "vetoed by sensitivity" in rendered


def test_reason_with_no_signals_still_renders():
    assert ResidencyReason(Residency.SYNC, ()).render() == (
        "no signal crossed a threshold"
    )


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------


def test_naive_timestamps_are_read_as_utc():
    assert parse_iso("2026-01-01T00:00:00").tzinfo is not None
