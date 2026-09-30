"""Trust policy: which sources a device is willing to hold, and which it believes.

Residency answers *where* a claim may live. This answers whether the device is
willing to have it in the answerable memory at all. The two are deliberately
separate instruments: a claim can stay local forever and still be refused, and a
claim can be perfectly well placed and still be held.

The policy is held here, on the engine's side of the boundary, and not in a
schema pack. A pack says what a vertical calls a claim and who outranks whom; it
does not get to decide what this particular device is prepared to believe, and
moving that decision into a pack would make "which sources do we trust" a
property of the industry rather than of the deployment.

Two ways to name a source as untrustworthy, either or both:

- ``held_classes`` — the source classes this device refuses to believe before
  something corroborates them. Named outright.
- ``entitlement_floor`` — the ladder rank below which a source is held. Data,
  not an ``if``, so tightening the gate is a version bump rather than a redeploy.

The default holds nothing. That is not an oversight: a device that silently
started quarantining a class the ladder ranks above another would change the
meaning of verdicts that were correct when they were rendered. The gate is
present, versioned and inert until an operator turns it on, and the reason it
held is stamped onto the claim it held so that a later reader can see the gate
was ever open.

Nothing here reads a clock, and nothing here is consulted anywhere but on
arrival. A quarantine therefore cannot lapse: not by waiting, not by a restart,
not by a reconnect. Letting a claim in is an action somebody takes, and it says
who and why.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from edgemem.domain import AuthorityClass, Claim, Ladder, Trust


@dataclass(frozen=True)
class TrustDecision:
    """Whether the gate lets a claim through, and what it had to weigh.

    Every signal is reported, including the ones that did not fire, so a
    disagreement about a quarantine can be settled by reading the decision rather
    than by re-deriving it. The same contract the residency reason keeps, for the
    same reason: a decision an operator cannot audit is a decision they have to
    trust.
    """

    trust: Trust
    signals: tuple[str, ...] = ()
    reason: str | None = None
    policy_version: str = "v1"

    @property
    def held(self) -> bool:
        return self.trust is Trust.QUARANTINED

    def to_payload(self) -> dict[str, Any]:
        return {
            "trust": self.trust.value,
            "signals": list(self.signals),
            "reason": self.reason,
            "policy_version": self.policy_version,
        }

    def render(self) -> str:
        parts = list(self.signals)
        if self.reason:
            parts.append(self.reason)
        return "; ".join(parts) if parts else "no trust gate was configured"


@dataclass(frozen=True)
class TrustPolicy:
    """Which sources this device holds, held as data.

    Frozen, because a quarantine a device cannot reproduce is indistinguishable
    from an arbitrary one. Tuning produces a new policy through
    :meth:`with_thresholds`; a device can still reproduce either decision
    afterwards because the reason travels with the claim.
    """

    policy_version: str = "v1"

    held_classes: frozenset[AuthorityClass] = frozenset()
    """Source classes refused outright, whatever they are corroborated by.

    Coerced on construction: a policy that holds a class by bare string, or holds
    one that is not a source class at all, would fail at the moment a claim
    depends on it rather than at the moment it was configured.
    """

    entitlement_floor: int | None = None
    """Ladder rank below which a source is held. ``None`` means no floor."""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "held_classes",
            frozenset(AuthorityClass(c) for c in self.held_classes),
        )
        if self.entitlement_floor is not None:
            object.__setattr__(self, "entitlement_floor", int(self.entitlement_floor))

    # -- the decision ------------------------------------------------------

    def decide(self, claim: Claim, ladder: Ladder) -> TrustDecision:
        """Judge one claim's source. Says what it weighed and why it ruled.

        The claim itself is not consulted. The gate is a statement about where an
        assertion came from, so a claim that arrives already marked quarantined
        keeps the mark and the reason its author gave — this decides what to do
        with a claim nobody has ruled on yet, and never overrules a ruling that
        exists.
        """
        rank = ladder.rank(claim.source_class)
        held_by_class = claim.source_class in self.held_classes
        held_by_rank = (
            self.entitlement_floor is not None and rank < self.entitlement_floor
        )
        signals = (
            _class_signal("source_class", claim.source_class, self.held_classes),
            _rank_signal("ladder_rank", rank, self.entitlement_floor),
        )
        if not (held_by_class or held_by_rank):
            return TrustDecision(
                trust=Trust.TRUSTED,
                signals=signals,
                policy_version=self.policy_version,
            )
        reasons = []
        if held_by_class:
            reasons.append(
                f"source class {claim.source_class.value} is named as low-trust"
            )
        if held_by_rank:
            reasons.append(
                f"source class {claim.source_class.value} ranks {rank}, below the "
                f"entitlement floor {self.entitlement_floor}"
            )
        return TrustDecision(
            trust=Trust.QUARANTINED,
            signals=signals,
            reason=f"held by trust policy {self.policy_version}: "
            + "; ".join(reasons),
            policy_version=self.policy_version,
        )

    # -- tuning ------------------------------------------------------------

    def with_thresholds(self, **overrides: Any) -> "TrustPolicy":
        """A new policy with fields replaced. No rebuild, no in-place edit."""
        return replace(self, **overrides)


DEFAULT_TRUST_POLICY = TrustPolicy()
"""What runs until an operator opens the gate.

Holds nothing. The gate exists in the engine so the judgement is made in one
place, from versioned data, rather than per-claim by whoever happened to record
it — but a deployment that has not said which sources it distrusts does not get
to have sources taken away from it by default.
"""


# --------------------------------------------------------------------------
# reason text
# --------------------------------------------------------------------------


def _class_signal(
    name: str, value: AuthorityClass, held: frozenset[AuthorityClass]
) -> str:
    """A class read against the held set, showing both.

    The held set is spelled out even when the class is not in it, so a decision
    to trust is as auditable as a decision to hold.
    """
    marker = "held" if value in held else "not held"
    named = ", ".join(sorted(c.value for c in held)) or "none"
    return f"{name} {value.value} {marker} (held classes: {named})"


def _rank_signal(name: str, value: int, floor: int | None) -> str:
    """A ladder rank read against a floor, showing both — or the absence of one."""
    if floor is None:
        return f"{name} {value} vs no entitlement floor"
    operator = ">=" if value >= floor else "<"
    return f"{name} {value} {operator} {floor}"