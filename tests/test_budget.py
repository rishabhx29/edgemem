"""Cumulative allowance over a set of claims.

The defect: each claim was classified against the same un-decremented context,
so a set of individually affordable claims could add up to several times the
allowance. "A byte budget the device respects" was not implemented for a set.
"""

from __future__ import annotations

import pytest

from edgemem.domain import Claim, Residency, utcnow
from edgemem.residency import (
    DEFAULT_POLICY,
    ResidencyContext,
    estimate_outbound_bytes,
)

BUDGET = 8 * 1024 * 1024


def context(**over) -> ResidencyContext:
    base = {
        "decided_at": utcnow(),
        "byte_budget": BUDGET,
        "remaining_bytes": BUDGET,
    }
    base.update(over)
    return ResidencyContext(**base)


def eager_claim(i: int, **over) -> Claim:
    """A claim the policy wants to sync: urgent, salient, not sensitive."""
    now = utcnow()
    base = {
        "subject": f"asset {i}",
        "attribute": "reading",
        "value": f"reading value {i} on unit {i}",
        "author": f"operator-{i}",
        "observer": "instrument",
        "device_id": "TRK-7",
        "observed_at": now,
        "recorded_at": now,
        "sensitivity": 0.0,
        "salience": 0.9,
        "urgency": 0.9,
    }
    base.update(over)
    return Claim(**base)


def test_a_large_set_cannot_exceed_the_spendable_allowance():
    """The regression: this planned 3.6x the allowance before the fix."""
    policy = DEFAULT_POLICY
    claims = [eager_claim(i) for i in range(2_000)]

    ctx = context()
    spendable = policy.spendable(ctx)
    planned = policy.planned_outbound_bytes(claims, ctx)

    assert planned <= spendable, (
        f"planned {planned} bytes against a spendable allowance of {spendable}"
    )


def test_the_plan_spends_down_as_it_goes():
    policy = DEFAULT_POLICY
    # Enough claims to actually exhaust the allowance, so the boundary is real.
    claims = [eager_claim(i) for i in range(20_000)]

    planned = policy.plan(claims, context())
    synced = [c for c, r in planned if r.residency is Residency.SYNC]
    held = [c for c, r in planned if r.residency is not Residency.SYNC]

    assert synced, "an eager set should sync something"
    assert held, "a large set must exhaust the allowance and hold the remainder"
    # It stops at the boundary rather than running past the end.
    assert len(synced) < len(claims)
    assert len(synced) + len(held) == len(claims)


def test_every_plan_entry_still_carries_a_reason():
    policy = DEFAULT_POLICY
    planned = policy.plan([eager_claim(i) for i in range(50)], context())
    for claim, reason in planned:
        assert reason.signals, f"{claim.claim_id} got no reason"
        assert reason.render()


def test_an_empty_allowance_plans_nothing_out():
    policy = DEFAULT_POLICY
    claims = [eager_claim(i) for i in range(50)]
    planned = policy.plan(claims, context(remaining_bytes=0, byte_budget=0))
    assert all(r.residency is not Residency.SYNC for _, r in planned)


def test_planning_is_pure_and_repeatable():
    policy = DEFAULT_POLICY
    claims = [eager_claim(i) for i in range(100)]
    ctx = context()
    first = policy.planned_outbound_bytes(claims, ctx)
    second = policy.planned_outbound_bytes(claims, ctx)
    assert first == second


def test_a_single_claim_still_decides_independently():
    """The per-claim path is unchanged; the set path layers on top."""
    policy = DEFAULT_POLICY
    c = eager_claim(1)
    assert policy.decide(c, context()).residency is Residency.SYNC


def test_estimate_is_close_to_the_serialised_claim():
    c = eager_claim(1)
    estimated = estimate_outbound_bytes(c)
    import json

    actual = len(json.dumps(c.to_payload(), default=str).encode("utf-8"))
    # The estimate carries the claim id and an envelope on top of the payload.
    assert estimated >= actual
    assert estimated < actual * 3, "the envelope should not dominate"
