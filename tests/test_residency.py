"""Residency policy behaviour, observed through its own public surface.

A test asserts on the classification, the reason text and the byte count — the
three things an operator reading the reason would see. Nothing here reaches into
the scoring, so the arithmetic stays free to change as long as the decisions and
their justifications hold.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from edgemem import domain
from edgemem.domain import Claim, Residency, utcnow
from edgemem.residency import (
    DEFAULT_POLICY,
    ResidencyContext,
    ResidencyPolicy,
    estimate_outbound_bytes,
    planned_outbound_bytes,
    redundancy_against_memory,
)

MOMENT = datetime(2026, 3, 4, 9, 15, tzinfo=timezone.utc)

NUMBER = r"-?\d+(?:\.\d+)?"
SIGNAL = re.compile(
    rf"^(?P<name>[a-z_]+) (?P<value>{NUMBER}) "
    rf"(?P<op><=|>=|<|>) (?P<threshold>{NUMBER})$"
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
        "sensitivity": 0.0,
        "salience": 0.5,
        "urgency": 0.5,
    }
    base.update(over)
    return Claim(**base)


def context(**over) -> ResidencyContext:
    base = {"decided_at": MOMENT}
    base.update(over)
    return ResidencyContext(**base)


def signal(reason, name: str) -> str:
    matches = [s for s in reason.signals if s.startswith(f"{name} ")]
    assert len(matches) == 1, f"one {name} signal expected in {reason.render()}"
    return matches[0]


# --------------------------------------------------------------------------
# the sensitivity veto
# --------------------------------------------------------------------------


def test_a_sensitive_claim_is_sealed_whatever_its_urgency():
    """A veto is not a weight. Urgency at its maximum does not move it."""
    reason = DEFAULT_POLICY.decide(
        make_claim(sensitivity=0.92, salience=0.95, urgency=0.99), context()
    )

    assert reason.residency is Residency.LOCAL
    assert reason.vetoed_by == "sensitivity"
    # the urgency that would have carried it is still on the record
    assert signal(reason, "urgency") == "urgency 0.99 >= 0.50"
    assert signal(reason, "sensitivity") == "sensitivity 0.92 >= 0.80"
    assert "vetoed by sensitivity" in reason.render()


def test_no_combination_of_other_signals_outvotes_the_veto():
    for salience in (0.0, 0.5, 1.0):
        for urgency in (0.0, 0.5, 1.0):
            for redundancy in (0.0, 0.5, 1.0):
                reason = DEFAULT_POLICY.decide(
                    make_claim(sensitivity=1.0, salience=salience, urgency=urgency),
                    context(redundancy=redundancy, remaining_bytes=8 * 1024 * 1024),
                )
                assert reason.residency is Residency.LOCAL
                assert reason.vetoed_by == "sensitivity"


def test_a_claim_just_below_the_veto_threshold_is_not_vetoed():
    policy = DEFAULT_POLICY.with_thresholds(sensitivity_veto_threshold=0.8)
    reason = policy.decide(
        make_claim(sensitivity=0.79, salience=0.9, urgency=0.9), context()
    )

    assert reason.vetoed_by is None
    assert signal(reason, "sensitivity") == "sensitivity 0.79 < 0.80"
    assert reason.residency is Residency.SYNC


def test_raising_the_veto_threshold_seals_more_claims():
    claim = make_claim(sensitivity=0.5, salience=0.9, urgency=0.9)
    assert DEFAULT_POLICY.decide(claim, context()).residency is Residency.SYNC

    stricter = DEFAULT_POLICY.with_thresholds(sensitivity_veto_threshold=0.4)
    reason = stricter.decide(claim, context())
    assert reason.residency is Residency.LOCAL
    assert reason.vetoed_by == "sensitivity"
    assert signal(reason, "sensitivity") == "sensitivity 0.50 >= 0.40"


# --------------------------------------------------------------------------
# the reason is mandatory
# --------------------------------------------------------------------------


def grid():
    for sensitivity in (0.0, 0.5, 0.95):
        for salience in (0.0, 0.6):
            for urgency in (0.0, 0.9):
                yield make_claim(
                    sensitivity=sensitivity, salience=salience, urgency=urgency
                )


def test_every_decision_carries_at_least_one_signal():
    for claim in grid():
        for ctx in (
            context(),
            context(remaining_bytes=1, byte_budget=8 * 1024 * 1024),
            context(depot_copies=1, redundancy=0.9),
        ):
            reason = DEFAULT_POLICY.decide(claim, ctx)
            assert reason.signals, f"no reason for {claim.claim_id}"
            assert reason.render()
            assert reason.render() != "no signal crossed a threshold"


def test_every_signal_names_its_value_and_its_threshold():
    for claim in grid():
        reason = DEFAULT_POLICY.decide(claim, context(redundancy=0.75))
        for text in reason.signals:
            match = SIGNAL.match(text)
            assert match, f"signal does not show its work: {text!r}"
            assert match["op"] in (">=", "<", "<=", ">")
            assert match["value"] and match["threshold"]


def test_the_reason_states_the_thresholds_the_decision_was_made_against():
    reason = DEFAULT_POLICY.decide(
        make_claim(sensitivity=0.0, salience=0.7, urgency=0.8),
        context(redundancy=0.25),
    )
    rendered = reason.render()
    for expected in (
        "sensitivity 0.00 < 0.80",
        "urgency 0.80 >= 0.50",
        "salience 0.70 >= 0.30",
        "redundancy 0.25 < 0.50",
    ):
        assert expected in rendered


def test_a_score_below_its_threshold_is_readable_as_such():
    reason = DEFAULT_POLICY.decide(make_claim(salience=0.35, urgency=0.1), context())
    assert signal(reason, "score").startswith("score ")
    assert " < " in signal(reason, "score")
    assert reason.residency is Residency.LOCAL


# --------------------------------------------------------------------------
# reproducibility
# --------------------------------------------------------------------------


def test_the_same_inputs_reproduce_the_same_decision():
    claim = make_claim(sensitivity=0.1, salience=0.6, urgency=0.7)
    ctx = context(
        redundancy=0.2, remaining_bytes=3_000_000, byte_budget=8 * 1024 * 1024
    )

    decisions = [DEFAULT_POLICY.decide(claim, ctx) for _ in range(8)]
    assert all(d == decisions[0] for d in decisions)
    assert all(d.to_payload() == decisions[0].to_payload() for d in decisions)


def test_a_separately_built_policy_with_the_same_values_agrees():
    claim = make_claim(salience=0.8, urgency=0.8)
    twin = ResidencyPolicy(
        policy_version=DEFAULT_POLICY.policy_version,
        urgency_threshold=DEFAULT_POLICY.urgency_threshold,
        sync_threshold=DEFAULT_POLICY.sync_threshold,
    )
    assert twin.decide(claim, context()) == DEFAULT_POLICY.decide(claim, context())


def test_the_policy_version_travels_with_the_reason():
    reason = DEFAULT_POLICY.with_thresholds(
        policy_version="v2-urgency-strict"
    ).decide(make_claim(), context())
    assert reason.policy_version == "v2-urgency-strict"
    assert reason.to_payload()["policy_version"] == "v2-urgency-strict"


def test_recency_is_not_a_factor():
    """A newer claim is not more worth sending, and an older one is not safer."""
    claim = make_claim(sensitivity=0.0, salience=0.6, urgency=0.9)
    earlier = context(decided_at=MOMENT - timedelta(days=900))
    later = context(decided_at=MOMENT + timedelta(days=900))

    assert (
        DEFAULT_POLICY.decide(claim, earlier)
        == DEFAULT_POLICY.decide(claim, later)
    )


def test_the_decision_path_reaches_no_network_and_no_clock(monkeypatch):
    import socket
    import time

    def refuse(*args, **kwargs):
        raise AssertionError("the residency policy must stay offline and pure")

    for opening in ("socket", "create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, opening, refuse)
    for reading in ("time", "monotonic", "perf_counter", "monotonic_ns"):
        monkeypatch.setattr(time, reading, refuse)
    monkeypatch.setattr(domain, "utcnow", refuse)

    reason = DEFAULT_POLICY.decide(
        make_claim(sensitivity=0.95, salience=0.8, urgency=0.8), context()
    )
    assert reason.residency is Residency.LOCAL
    assert reason.vetoed_by == "sensitivity"


# --------------------------------------------------------------------------
# the byte budget
# --------------------------------------------------------------------------


def test_a_device_near_its_allowance_keeps_more_local():
    claim = make_claim(salience=0.9, urgency=0.9)
    flush = context(byte_budget=8 * 1024 * 1024, remaining_bytes=8 * 1024 * 1024)
    starved = context(byte_budget=8 * 1024 * 1024, remaining_bytes=100_000)

    assert DEFAULT_POLICY.decide(claim, flush).residency is Residency.SYNC

    starved_reason = DEFAULT_POLICY.decide(claim, starved)
    assert starved_reason.residency is Residency.LOCAL
    assert " > " in signal(starved_reason, "outbound_bytes")
    assert " < " in signal(starved_reason, "budget_headroom")


def test_the_budget_leaves_the_work_unchanged_and_only_the_allowance():
    claim = make_claim(salience=0.9, urgency=0.9)
    flush = context(byte_budget=8 * 1024 * 1024, remaining_bytes=8 * 1024 * 1024)
    starved = context(byte_budget=8 * 1024 * 1024, remaining_bytes=100_000)

    cost = estimate_outbound_bytes(claim)
    assert f"outbound_bytes {cost} <=" in signal(
        DEFAULT_POLICY.decide(claim, flush), "outbound_bytes"
    )
    assert f"outbound_bytes {cost} >" in signal(
        DEFAULT_POLICY.decide(claim, starved), "outbound_bytes"
    )


def test_the_reserve_is_held_back_even_when_the_allowance_is_untouched():
    claim = make_claim(salience=0.9, urgency=0.9)
    policy = DEFAULT_POLICY.with_thresholds(reserve_fraction=0.0)
    ctx = context(byte_budget=1_000_000, remaining_bytes=1_000_000)

    assert DEFAULT_POLICY.decide(claim, ctx).residency is Residency.SYNC
    assert policy.spendable(ctx) == 1_000_000
    assert DEFAULT_POLICY.spendable(ctx) == 900_000


def test_a_claim_larger_than_the_whole_allowance_stays_local():
    enormous = make_claim(value="x" * 200_000, salience=1.0, urgency=1.0)
    tight = context(byte_budget=100_000, remaining_bytes=100_000)

    assert estimate_outbound_bytes(enormous) > 100_000

    reason = DEFAULT_POLICY.decide(enormous, tight)
    assert reason.residency is Residency.LOCAL
    assert " > " in signal(reason, "outbound_bytes")


def test_the_reported_outbound_cost_of_a_policy_changes_with_that_policy():
    claims = [make_claim(salience=0.5, urgency=0.4) for _ in range(3)]
    ctx = context()

    held_by_default = DEFAULT_POLICY.with_thresholds(urgency_threshold=0.30)
    assert planned_outbound_bytes(claims, ctx) == 0
    assert planned_outbound_bytes(claims, ctx, held_by_default) == sum(
        estimate_outbound_bytes(c) for c in claims
    )


def test_an_empty_allowance_sends_nothing():
    claims = [make_claim(salience=1.0, urgency=1.0) for _ in range(3)]
    spent = context(byte_budget=8 * 1024 * 1024, remaining_bytes=0)
    assert planned_outbound_bytes(claims, spent) == 0
    assert all(
        DEFAULT_POLICY.decide(c, spent).residency is Residency.LOCAL for c in claims
    )


# --------------------------------------------------------------------------
# operator-adjustable urgency
# --------------------------------------------------------------------------


def test_the_urgency_threshold_is_the_operators_dial():
    claim = make_claim(salience=0.5, urgency=0.4)

    held = DEFAULT_POLICY.decide(claim, context())
    assert held.residency is Residency.LOCAL
    assert signal(held, "urgency") == "urgency 0.40 < 0.50"

    opened = DEFAULT_POLICY.with_thresholds(urgency_threshold=0.30).decide(
        claim, context()
    )
    assert opened.residency is Residency.SYNC
    assert signal(opened, "urgency") == "urgency 0.40 >= 0.30"
    assert opened.render() != held.render()


def test_the_urgency_weight_is_configurable_too():
    claim = make_claim(salience=0.4, urgency=1.0)
    assert DEFAULT_POLICY.decide(claim, context()).residency is Residency.SYNC

    muted = DEFAULT_POLICY.with_thresholds(urgency_weight=0.0)
    reason = muted.decide(claim, context())
    assert reason.residency is Residency.LOCAL
    assert "urgency 1.00 >= 0.50" in reason.render()


def test_thresholds_can_be_changed_without_rebuilding_the_policy():
    tuned = DEFAULT_POLICY.with_thresholds(
        sync_threshold=2.00, policy_version="v3-uplink-too-expensive"
    )
    assert tuned is not DEFAULT_POLICY
    assert DEFAULT_POLICY.sync_threshold == 0.55
    loudest = make_claim(salience=1.0, urgency=1.0)
    assert DEFAULT_POLICY.decide(loudest, context()).residency is Residency.SYNC
    # nothing the device can observe scores this high, so nothing moves
    assert tuned.decide(loudest, context()).residency is Residency.LOCAL


# --------------------------------------------------------------------------
# the device's own memory
# --------------------------------------------------------------------------


def test_a_device_already_crowded_with_the_subject_holds_back():
    claim = make_claim(salience=0.6, urgency=0.0)
    uncrowded = DEFAULT_POLICY.decide(claim, context(redundancy=0.0))
    assert uncrowded.residency is Residency.SYNC

    crowded = DEFAULT_POLICY.decide(claim, context(redundancy=0.8))
    assert crowded.residency is Residency.LOCAL
    assert "redundancy 0.80 >= 0.50" in crowded.render()


def test_redundancy_is_measured_against_local_memory():
    held = [
        make_claim(),
        make_claim(value="intact"),
        make_claim(subject="reefer probe", attribute="temperature"),
    ]
    seal = redundancy_against_memory(held, "trailer T-114 seal", "state")
    probe = redundancy_against_memory(held, "reefer probe", "temperature")
    assert seal == pytest.approx(2 / 3)
    assert probe == pytest.approx(1 / 3)
    assert redundancy_against_memory([], "trailer T-114 seal", "state") == 0.0


def test_recency_does_not_change_measured_redundancy():
    now = utcnow()
    held = [
        make_claim(observed_at=now - timedelta(days=900)),
        make_claim(value="intact", observed_at=now - timedelta(days=2)),
    ]
    assert redundancy_against_memory(held, "trailer T-114 seal", "state") == 1.0


# --------------------------------------------------------------------------
# all three classifications are reachable
# --------------------------------------------------------------------------


def test_local_sync_and_cloud_only_are_all_reachable():
    flush = context(byte_budget=8 * 1024 * 1024, remaining_bytes=8 * 1024 * 1024)
    reached = {
        DEFAULT_POLICY.decide(
            make_claim(sensitivity=0.92, urgency=1.0), flush
        ).residency,
        DEFAULT_POLICY.decide(
            make_claim(salience=0.9, urgency=0.9), flush
        ).residency,
        DEFAULT_POLICY.decide(
            make_claim(salience=0.9, urgency=0.9), context(remaining_bytes=1_000)
        ).residency,
        DEFAULT_POLICY.decide(
            make_claim(), context(depot_copies=1)
        ).residency,
    }
    assert reached == {Residency.LOCAL, Residency.SYNC, Residency.CLOUD_ONLY}


def test_a_claim_the_depot_already_holds_is_cited_rather_than_resent():
    claim = make_claim(salience=0.9, urgency=0.9)
    reason = DEFAULT_POLICY.decide(claim, context(depot_copies=1))

    assert reason.residency is Residency.CLOUD_ONLY
    assert reason.vetoed_by is None
    assert signal(reason, "depot_copies") == "depot_copies 1 >= 1"
    assert planned_outbound_bytes([claim], context(depot_copies=1)) == 0


def test_a_claim_the_depot_has_never_seen_is_not_cloud_only():
    reason = DEFAULT_POLICY.decide(make_claim(salience=0.9, urgency=0.9), context())
    assert reason.residency is Residency.SYNC
    assert signal(reason, "depot_copies") == "depot_copies 0 < 1"


def test_a_claim_becomes_cloud_only_once_the_policy_says_so():
    claim = make_claim(salience=0.9, urgency=0.9)
    decided_locally = DEFAULT_POLICY.with_thresholds(cloud_only_copies=2)
    reason = decided_locally.decide(claim, context(depot_copies=1))

    assert DEFAULT_POLICY.decide(claim, context(depot_copies=1)).residency is (
        Residency.CLOUD_ONLY
    )
    assert reason.residency is Residency.SYNC
    assert signal(reason, "depot_copies") == "depot_copies 1 < 2"


def test_a_sealed_claim_is_local_even_when_the_depot_already_has_it():
    reason = DEFAULT_POLICY.decide(
        make_claim(sensitivity=0.9, salience=1.0, urgency=1.0),
        context(depot_copies=1),
    )
    assert reason.residency is Residency.LOCAL
    assert reason.vetoed_by == "sensitivity"


def test_a_plain_claim_syncs():
    reason = DEFAULT_POLICY.decide(make_claim(), context())
    assert reason.residency is Residency.SYNC
    assert reason.vetoed_by is None


# --------------------------------------------------------------------------
# the byte count
# --------------------------------------------------------------------------


def test_an_outbound_cost_is_always_reported_and_positive():
    claim = make_claim()
    cost = estimate_outbound_bytes(claim)
    reason = DEFAULT_POLICY.decide(claim, context())
    assert cost > 0
    assert f"outbound_bytes {cost} <=" in signal(reason, "outbound_bytes")


def test_a_longer_claim_costs_more_to_send():
    short = estimate_outbound_bytes(make_claim(value="broken"))
    long = estimate_outbound_bytes(make_claim(value="broken " * 200))
    assert long > short


def test_the_cost_does_not_change_when_the_claim_does():
    claim = make_claim()
    assert estimate_outbound_bytes(claim) == estimate_outbound_bytes(claim)
    assert estimate_outbound_bytes(claim) > estimate_outbound_bytes(
        make_claim(value="")
    )


def test_every_claim_in_a_set_gets_exactly_one_classification():
    claims = list(grid())
    ctx = context(depot_copies=0, remaining_bytes=8 * 1024 * 1024)
    decided = [DEFAULT_POLICY.decide(c, ctx) for c in claims]

    assert len(decided) == len(claims)
    assert all(d.residency in set(Residency) for d in decided)
    assert all(d.signals for d in decided)
