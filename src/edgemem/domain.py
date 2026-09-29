"""Core domain vocabulary for the edge memory engine.

A **claim** is an assertion somebody made. It is not a fact. This distinction is
load-bearing: it is why the engine refuses to pick a winner between two claims
about the same subject, and why a supersession is recorded rather than performed.

Nothing in this module knows what industry it is serving. Vertical-specific
language arrives through a schema pack.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def parse_iso(text: str) -> datetime:
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------


class Residency(str, Enum):
    """Where a claim is permitted to live."""

    LOCAL = "LOCAL"
    """Never leaves the device."""

    SYNC = "SYNC"
    """Permitted to reach the depot."""

    CLOUD_ONLY = "CLOUD_ONLY"
    """Supposed to already be at the depot; not answerable locally."""


class VerdictKind(str, Enum):
    """The one thing a question can come back as."""

    ANSWERED_LOCALLY = "ANSWERED_LOCALLY"
    CONFLICTED = "CONFLICTED"
    UNRESOLVED_CLOUD_REQUIRED = "UNRESOLVED_CLOUD_REQUIRED"
    CORRECTED = "CORRECTED"


class AuthorityClass(str, Enum):
    """How a source is entitled to be believed. Ordered by the ladder, not here."""

    INSTRUMENT = "INSTRUMENT"
    """A calibrated device that produced the observation directly."""

    ATTESTED_HUMAN = "ATTESTED_HUMAN"
    """A named person signing for what they saw."""

    UNATTESTED_HUMAN = "UNATTESTED_HUMAN"
    """A person without signing authority for this subject."""

    THIRD_PARTY_FEED = "THIRD_PARTY_FEED"
    """An upstream system relaying something it did not observe."""

    RUMOUR = "RUMOUR"
    """Unattributed. Held pending corroboration."""


class Trust(str, Enum):
    TRUSTED = "TRUSTED"
    QUARANTINED = "QUARANTINED"


# --------------------------------------------------------------------------
# causal metadata
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CausalContext:
    """Enough metadata to tell concurrency from sequence.

    Bounded by the number of contributing devices, not by the number of edits:
    ``versions`` holds one counter per device that has ever written on this subject.
    """

    versions: dict[str, int] = field(default_factory=dict)

    def observe(self, device_id: str) -> None:
        """Record that a write by ``device_id`` has happened."""
        self.versions[device_id] = self.versions.get(device_id, 0) + 1

    def copy_for_write(self, device_id: str) -> "CausalContext":
        """Return the context a device should stamp on a new claim."""
        return CausalContext(dict(self.versions))

    def ge(self, other: "CausalContext") -> bool:
        """True when every device counter here is >= the one there."""
        keys = set(self.versions) | set(other.versions)
        return all(self.versions.get(d, 0) >= other.versions.get(d, 0) for d in keys)

    def is_concurrent_with(self, other: "CausalContext") -> bool:
        """True when neither side saw the other's write.

        The dotted-version-vector test: two contexts are concurrent exactly when
        neither dominates the other, where dominance is the weak ordering ">="
        together with at least one strict increase. Equal contexts are therefore
        NOT concurrent — they describe the same point in history.
        """
        return not self.ge(other) and not other.ge(self)

    def dominates(self, other: "CausalContext") -> bool:
        """True when this context is strictly causally after ``other``."""
        return self.ge(other) and not other.ge(self)

    def merged_with(self, other: "CausalContext") -> "CausalContext":
        """Least upper bound: per-device maximum."""
        keys = set(self.versions) | set(other.versions)
        return CausalContext(
            {
                d: max(self.versions.get(d, 0), other.versions.get(d, 0))
                for d in keys
            }
        )

    def to_payload(self) -> dict[str, int]:
        return dict(self.versions)

    @staticmethod
    def from_payload(raw: dict[str, int] | None) -> "CausalContext":
        return CausalContext(dict(raw or {}))


# --------------------------------------------------------------------------
# the claim
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """An assertion somebody made, about one subject, on one attribute.

    A claim is never overwritten. A later claim about the same subject and
    attribute is a *new* claim, and the relationship between them is recorded
    so the previous value stays visible.
    """

    subject: str
    """What the claim is about."""

    attribute: str
    """The aspect of the subject being asserted."""

    value: str
    """The asserted value, as the author stated it."""

    author: str
    """Who made the assertion."""

    observer: str
    """The instrument or role that perceived it."""

    device_id: str
    """The device that holds it."""

    observed_at: datetime
    """When it was perceived. Not when it was transmitted."""

    recorded_at: datetime
    """When the device stored it."""

    claim_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source_class: AuthorityClass = AuthorityClass.UNATTESTED_HUMAN
    trust: Trust = Trust.TRUSTED
    causal: CausalContext = field(default_factory=CausalContext)
    supersedes: str | None = None
    """Claim id this one revises. The earlier claim stays readable."""

    sensitivity: float = 0.0
    """0.0 to 1.0. Acts as a veto on sync, not as a weight."""

    salience: float = 0.5
    """0.0 to 1.0. How much it matters for answering."""

    urgency: float = 0.5
    """0.0 to 1.0. Operator-adjustable."""

    quarantine_reason: str | None = None

    def __post_init__(self) -> None:
        # Claims arrive from a depot over a sync, so they are untrusted input.
        # Coerce the enums rather than trusting the caller to have used them:
        # a bare string that slips through would make is_quarantined() lie.
        object.__setattr__(
            self, "trust", Trust(self.trust)
        )
        object.__setattr__(
            self, "source_class", AuthorityClass(self.source_class)
        )
        if isinstance(self.causal, dict):
            object.__setattr__(self, "causal", CausalContext(self.causal))
        for name in ("sensitivity", "salience", "urgency"):
            object.__setattr__(self, name, float(getattr(self, name)))
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(
                    f"{name} must be between 0.0 and 1.0, got {getattr(self, name)!r}"
                )

    def with_trust(self, trust: Trust, reason: str | None) -> "Claim":
        return replace(self, trust=trust, quarantine_reason=reason)

    def is_quarantined(self) -> bool:
        return self.trust == Trust.QUARANTINED

    def to_payload(self) -> dict[str, Any]:
        """Serialise for the vector store. Every field is indexed, not embedded.

        The claim id travels in the payload as well as being used to derive the
        point id. The point id is a storage key the store chose; the claim id is
        the claim's identity. Keeping them apart means a read path never has to
        guess that a numeric id is the claim's own.
        """
        return {
            "claim_id": self.claim_id,
            "subject": self.subject,
            "attribute": self.attribute,
            "value": self.value,
            "author": self.author,
            "observer": self.observer,
            "device_id": self.device_id,
            "observed_at": iso(self.observed_at),
            "recorded_at": iso(self.recorded_at),
            "source_class": self.source_class.value,
            "trust": self.trust.value,
            "causal": self.causal.to_payload(),
            "supersedes": self.supersedes,
            "sensitivity": float(self.sensitivity),
            "salience": float(self.salience),
            "urgency": float(self.urgency),
            "quarantine_reason": self.quarantine_reason,
        }

    @staticmethod
    def from_payload(
        claim_id: str, payload: dict[str, Any]
    ) -> "Claim":
        """Rebuild a claim.

        Prefers the claim id carried in the payload, because a caller reading
        from the store has only the record in hand. The argument is the
        fallback for a payload that predates the field.
        """
        return Claim(
            claim_id=payload.get("claim_id") or claim_id,
            subject=payload["subject"],
            attribute=payload["attribute"],
            value=payload["value"],
            author=payload["author"],
            observer=payload["observer"],
            device_id=payload.get("device_id", "unknown"),
            observed_at=parse_iso(payload["observed_at"]),
            recorded_at=parse_iso(payload["recorded_at"]),
            source_class=AuthorityClass(
                payload.get("source_class", AuthorityClass.UNATTESTED_HUMAN.value)
            ),
            trust=Trust(payload.get("trust", Trust.TRUSTED.value)),
            causal=CausalContext.from_payload(payload.get("causal")),
            supersedes=payload.get("supersedes"),
            sensitivity=float(payload.get("sensitivity", 0.0)),
            salience=float(payload.get("salience", 0.5)),
            urgency=float(payload.get("urgency", 0.5)),
            quarantine_reason=payload.get("quarantine_reason"),
        )

    def text_for_matching(self) -> str:
        """The text the retrieval paths index and search."""
        return f"{self.subject} {self.attribute} {self.value} {self.observer}"


# --------------------------------------------------------------------------
# verdicts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidencyReason:
    """Why a claim was classified the way it was.

    Every signal that fired is named with the value it held and the threshold it
    crossed, so the decision can be challenged rather than merely believed.
    """

    residency: Residency
    signals: tuple[str, ...]
    vetoed_by: str | None = None
    policy_version: str = "v1"

    def to_payload(self) -> dict[str, Any]:
        return {
            "residency": self.residency.value,
            "signals": list(self.signals),
            "vetoed_by": self.vetoed_by,
            "policy_version": self.policy_version,
        }

    def render(self) -> str:
        parts = list(self.signals)
        if self.vetoed_by:
            parts.append(f"vetoed by {self.vetoed_by}")
        return "; ".join(parts) if parts else "no signal crossed a threshold"


@dataclass(frozen=True)
class CitedClaim:
    """A claim as the caller sees it: content plus attribution."""

    claim_id: str
    subject: str
    attribute: str
    value: str
    author: str
    observer: str
    device_id: str
    observed_at: str
    source_class: str
    trust: str
    residency: str
    reason: str
    corroborations: int = 1
    stale: bool = False

    @staticmethod
    def of(claim: Claim, residency: ResidencyReason) -> "CitedClaim":
        return CitedClaim(
            claim_id=claim.claim_id,
            subject=claim.subject,
            attribute=claim.attribute,
            value=claim.value,
            author=claim.author,
            observer=claim.observer,
            device_id=claim.device_id,
            observed_at=iso(claim.observed_at),
            source_class=claim.source_class.value,
            trust=claim.trust.value,
            residency=residency.residency.value,
            reason=residency.render(),
        )


@dataclass(frozen=True)
class AuthorityView:
    """What the ladder says about who could settle a conflict.

    Presented, never applied. The engine escalates; a person decides.
    """

    ladder_version: str
    ordered_classes: tuple[str, ...]
    entitling_class: str | None
    note: str


@dataclass(frozen=True)
class ConflictSide:
    """One assertion in a disagreement, with how much supports it."""

    claim: CitedClaim
    supporters: int
    entitling: bool


@dataclass(frozen=True)
class NeededClaim:
    """What the device would need in order to answer, and what it would cost."""

    claim_id: str
    subject: str
    attribute: str
    value: str
    why_withheld: str
    bytes_if_sent: int
    device_id: str


@dataclass(frozen=True)
class Verdict:
    """The single output of the question interface.

    Exactly one of the four kinds is returned. The shape does not change with
    the kind, so a caller never branches on which fields exist.
    """

    kind: VerdictKind
    subject: str
    question: str
    summary: str
    claims: tuple[CitedClaim, ...] = ()
    conflicts: tuple[tuple[ConflictSide, ConflictSide], ...] = ()
    authority: AuthorityView | None = None
    needed: tuple[NeededClaim, ...] = ()
    previous_summary: str | None = None
    changed_by: str | None = None
    latency_ms: float = 0.0
    paths_used: tuple[str, ...] = ()
    bytes_withheld: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject": self.subject,
            "question": self.question,
            "summary": self.summary,
            "claims": [vars(c) for c in self.claims],
            "conflicts": [
                {
                    "a": vars(a.claim),
                    "a_supporters": a.supporters,
                    "a_entitling": a.entitling,
                    "b": vars(b.claim),
                    "b_supporters": b.supporters,
                    "b_entitling": b.entitling,
                }
                for a, b in self.conflicts
            ],
            "authority": vars(self.authority) if self.authority else None,
            "needed": [vars(n) for n in self.needed],
            "previous_summary": self.previous_summary,
            "changed_by": self.changed_by,
            "latency_ms": round(self.latency_ms, 4),
            "paths_used": list(self.paths_used),
            "bytes_withheld": self.bytes_withheld,
        }
