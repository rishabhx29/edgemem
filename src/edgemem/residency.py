"""Residency policy: where a claim is permitted to live, and why.

A deterministic scorer over the five signals a fleet operator reasons about —
sensitivity, urgency, salience, redundancy against the device's own memory, and
the remaining byte budget — plus what the depot already holds, which is what
separates a claim worth sending from one worth citing.

Sensitivity is a veto rather than a weight: a weight can be outvoted by a larger
one, and a veto cannot. Nothing else in this module can move a claim across the
sensitivity threshold.

Every decision carries a reason naming each signal evaluated, the value it held,
and the threshold it was measured against — including the signals that did not
fire, so the operator can see what it would have taken. The reason is part of
the decision rather than a log of it: an operator who disagrees with a
classification reads the reason and adjusts a threshold, instead of guessing at
the arithmetic.

The policy is pure. It reads no clock, opens no socket and draws no random
number, so the same claim, the same device state and the same policy version
reproduce the same classification. That is what makes a decision reproducible
rather than merely repeatable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterable, Sequence

from edgemem.domain import Claim, Residency, ResidencyReason

# --------------------------------------------------------------------------
# cost of a claim on the wire
# --------------------------------------------------------------------------

ENVELOPE_OVERHEAD_BYTES = 96
"""Framing the serialised payload does not describe: the depot's reply slot, the
record framing, and the acknowledgement."""


def estimate_outbound_bytes(claim: Claim) -> int:
    """What sending one claim would cost.

    The serialised payload is measured rather than estimated, the claim id is
    added because the depot needs it to make replay idempotent, and a fixed
    framing allowance covers the rest.
    """
    body = json.dumps(claim.to_payload(), separators=(",", ":"), default=str)
    return (
        len(body.encode("utf-8"))
        + len(claim.claim_id.encode("utf-8"))
        + ENVELOPE_OVERHEAD_BYTES
    )


def redundancy_against_memory(
    held: Sequence[Claim], subject: str, attribute: str
) -> float:
    """The share of the device's memory that already concerns this subject.

    A device already crowded with claims about one subject gains little from
    sending another, so the policy reads the share rather than the raw count and
    the figure stays comparable across devices of different sizes. Recency is
    not consulted: an old claim counts exactly as much as a new one.
    """
    if not held:
        return 0.0
    matching = sum(
        1 for c in held if c.subject == subject and c.attribute == attribute
    )
    return matching / len(held)


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidencyContext:
    """What the device knows about itself when it classifies a claim.

    Held here rather than read from the device, so a decision is a function of
    values that can be written down and replayed.
    """

    decided_at: datetime
    """When the device made the call. Recorded, never scored: recency is not a
    factor, and nothing in this module reads this field."""

    byte_budget: int = 8 * 1024 * 1024
    """The device's outbound allowance for the current window."""

    remaining_bytes: int = 8 * 1024 * 1024
    """What is left of that allowance."""

    redundancy: float = 0.0
    """The share of the device's memory already concerning this subject and
    attribute. See :func:`redundancy_against_memory`."""

    depot_copies: int = 0
    """Copies of this assertion that the device's own record shows at the depot."""


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidencyPolicy:
    """Thresholds and weights, held as data.

    Frozen, because a decision is only reproducible if the policy that produced
    it cannot move underneath the reason. Tuning produces a new policy with
    :meth:`with_thresholds`; a device running both versions can reproduce either
    decision afterwards.
    """

    policy_version: str = "v1"

    sensitivity_veto_threshold: float = 0.80
    """At or above this the claim is sealed locally and nothing else is
    consulted. The one threshold that is not a weight."""

    salience_threshold: float = 0.30
    """Below this, salience does not speak at all rather than speaking faintly."""

    salience_weight: float = 1.00

    urgency_threshold: float = 0.50
    """The operator's dial. Raising it holds back more, trading completeness
    against the device's data allowance."""

    urgency_weight: float = 0.60

    redundancy_threshold: float = 0.50
    """Below this, the device's own memory is not a reason to hold anything
    back."""

    redundancy_weight: float = 0.40

    sync_threshold: float = 0.55
    """The weighted score at which a claim is worth the bytes."""

    reserve_fraction: float = 0.10
    """The share of the allowance held back regardless of what any one claim is
    worth, so a long offline period cannot be spent down by the last claim."""

    cloud_only_copies: int = 1
    """Copies the depot must already hold before the device cites a claim from
    there instead of carrying an answerable copy itself."""

    # -- measurement --------------------------------------------------------

    def headroom(self, context: ResidencyContext) -> float:
        """The allowance left, as a share of the allowance."""
        if context.byte_budget <= 0:
            return 1.0 if context.remaining_bytes > 0 else 0.0
        return context.remaining_bytes / context.byte_budget

    def spendable(self, context: ResidencyContext) -> int:
        """What may actually be sent, once the reserve is held back."""
        reserve = int(context.byte_budget * self.reserve_fraction)
        return max(0, context.remaining_bytes - reserve)

    # -- scoring -----------------------------------------------------------

    def score(self, claim: Claim, context: ResidencyContext) -> float:
        """The weighted pull toward the depot.

        A signal that does not clear its own threshold contributes nothing
        rather than a smaller amount, so a threshold reads as an on/off switch
        its operator can see and set. Only redundancy pushes the other way: a
        device already holding this subject has the least to gain from sending
        it again.
        """
        total = 0.0
        if claim.salience >= self.salience_threshold:
            total += self.salience_weight * claim.salience
        if claim.urgency >= self.urgency_threshold:
            total += self.urgency_weight * claim.urgency
        if context.redundancy >= self.redundancy_threshold:
            total -= self.redundancy_weight * context.redundancy
        return total

    def is_vetoed(self, claim: Claim) -> bool:
        return claim.sensitivity >= self.sensitivity_veto_threshold

    # -- the decision ------------------------------------------------------

    def decide(self, claim: Claim, context: ResidencyContext) -> ResidencyReason:
        """Classify one claim, and say why.

        Evaluation is deliberately separate from classification: every signal is
        reported even when an earlier one has already settled the outcome. A veto
        that hid the urgency it overrode would be a veto nobody could challenge.
        """
        cost = estimate_outbound_bytes(claim)
        spendable = self.spendable(context)
        score = self.score(claim, context)
        vetoed_by = "sensitivity" if self.is_vetoed(claim) else None

        signals = (
            _score_signal(
                "sensitivity", claim.sensitivity, self.sensitivity_veto_threshold
            ),
            _count_signal(
                "depot_copies", context.depot_copies, self.cloud_only_copies
            ),
            _score_signal("urgency", claim.urgency, self.urgency_threshold),
            _score_signal("salience", claim.salience, self.salience_threshold),
            _score_signal(
                "redundancy", context.redundancy, self.redundancy_threshold
            ),
            _score_signal(
                "budget_headroom", self.headroom(context), self.reserve_fraction
            ),
            _allowance_signal("outbound_bytes", cost, spendable),
            _score_signal("score", score, self.sync_threshold),
        )

        if vetoed_by:
            residency = Residency.LOCAL
        elif context.depot_copies >= self.cloud_only_copies:
            residency = Residency.CLOUD_ONLY
        elif score >= self.sync_threshold and cost <= spendable:
            residency = Residency.SYNC
        else:
            residency = Residency.LOCAL

        return ResidencyReason(
            residency=residency,
            signals=signals,
            vetoed_by=vetoed_by,
            policy_version=self.policy_version,
        )

    def plan(
        self, claims: Iterable[Claim], context: ResidencyContext
    ) -> list[tuple[Claim, ResidencyReason]]:
        """Classify a set of claims against one shared allowance.

        The allowance is spent as it is committed. Classifying each claim
        independently against the same context would let a set of individually
        affordable claims add up to several times the allowance — which is the
        failure "a byte budget the device respects" is supposed to prevent.

        Order is preserved. Later claims see less headroom than earlier ones, and
        the reason for each says so.
        """
        planned: list[tuple[Claim, ResidencyReason]] = []
        remaining = context.remaining_bytes
        for claim in claims:
            cost = estimate_outbound_bytes(claim)
            spent_context = replace(context, remaining_bytes=remaining)
            reason = self.decide(claim, spent_context)
            if reason.residency is Residency.SYNC:
                remaining = max(0, remaining - cost)
            planned.append((claim, reason))
        return planned

    def planned_outbound_bytes(
        self, claims: Iterable[Claim], context: ResidencyContext
    ) -> int:
        """What a set of claims would cost the allowance under this policy.

        The figure a fleet operator wants before adopting a policy: the cost of
        the policy, measured by what it would actually move. Never exceeds the
        spendable allowance.
        """
        return sum(
            estimate_outbound_bytes(c)
            for c, r in self.plan(claims, context)
            if r.residency is Residency.SYNC
        )

    # -- tuning ------------------------------------------------------------

    def with_thresholds(self, **overrides: Any) -> "ResidencyPolicy":
        """A new policy with fields replaced. No rebuild, no in-place edit."""
        return replace(self, **overrides)


DEFAULT_POLICY = ResidencyPolicy()
"""The policy a device runs until an operator replaces it."""


def planned_outbound_bytes(
    claims: Iterable[Claim],
    context: ResidencyContext,
    policy: ResidencyPolicy | None = None,
) -> int:
    """The cost of a policy over a set of claims, under the default policy
    unless another is named."""
    return (policy or DEFAULT_POLICY).planned_outbound_bytes(claims, context)


# --------------------------------------------------------------------------
# reason text
# --------------------------------------------------------------------------


def _score_signal(name: str, value: float, threshold: float) -> str:
    """A scored signal read against a threshold, showing both."""
    operator = ">=" if value >= threshold else "<"
    return f"{name} {value:.2f} {operator} {threshold:.2f}"


def _count_signal(name: str, value: int, threshold: int) -> str:
    """A counted signal read against a threshold, showing both."""
    operator = ">=" if value >= threshold else "<"
    return f"{name} {value} {operator} {threshold}"


def _allowance_signal(name: str, value: int, threshold: int) -> str:
    """A byte figure read against an allowance, showing both."""
    operator = "<=" if value <= threshold else ">"
    return f"{name} {value} {operator} {threshold}"
