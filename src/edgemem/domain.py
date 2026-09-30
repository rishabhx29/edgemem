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
from typing import Any, Iterable

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
# authority
# --------------------------------------------------------------------------


DEFAULT_LADDER_NOTE = (
    "Presented for a person to settle. The engine does not choose between "
    "disagreeing claims, and recency is not an input."
)


def default_ladder() -> tuple[tuple[AuthorityClass, int], ...]:
    """Source classes in descending order of entitlement to be believed.

    Data, not code. A vertical replaces the whole ordering through its schema
    pack; the engine does not change. Recency is not a member of this ordering
    and is not consulted.
    """
    return (
        (AuthorityClass.INSTRUMENT, 100),
        (AuthorityClass.ATTESTED_HUMAN, 80),
        (AuthorityClass.UNATTESTED_HUMAN, 50),
        (AuthorityClass.THIRD_PARTY_FEED, 30),
        (AuthorityClass.RUMOUR, 10),
    )


@dataclass
class Ladder:
    """A versioned ordering of who could settle a conflict.

    Lives here rather than beside the question interface because a vertical has
    to be able to name a ladder before it can bind to the engine at all, and a
    pack that could only be written from inside the seam would make every new
    vertical an engine change.

    The optional ``note`` is presentation a vertical supplies in place of the
    engine's own framing of what the ladder is for. It changes nothing about
    how the ladder ranks; it changes only the words shown to whoever settles
    the disagreement.
    """

    version: str = "v1"
    entries: tuple[tuple[AuthorityClass, int], ...] = default_ladder()
    note: str | None = None

    def rank(self, source_class: AuthorityClass) -> int:
        for cls, weight in self.entries:
            if cls is source_class:
                return weight
        return 0

    def ordered(self) -> tuple[str, ...]:
        return tuple(cls.value for cls, _ in self.entries)

    def entitling_class(self, sides: Iterable[Claim]) -> str | None:
        """The highest-ranked source class present among the sides.

        Reported, never applied. The engine escalates; a person decides.
        """
        best: str | None = None
        best_rank = -1
        for claim in sides:
            rank = self.rank(claim.source_class)
            if rank > best_rank:
                best_rank, best = rank, claim.source_class.value
        return best

    def view(self, sides: Iterable[Claim]) -> AuthorityView:
        sides = list(sides)
        return AuthorityView(
            ladder_version=self.version,
            ordered_classes=self.ordered(),
            entitling_class=self.entitling_class(sides),
            note=self.note or DEFAULT_LADDER_NOTE,
        )


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

    release_note: str | None = None
    """What the operator said when they released this claim from quarantine.

    Kept beside ``quarantine_reason`` rather than overwriting it. Both remain true
    — the claim was held, and it was let go — and a reader who can only see the
    released claim still learns that a gate once stood in front of it and what the
    gate said. This is a judgement about one claim, recorded where the claim is.
    """

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

    def with_release(self, note: str) -> "Claim":
        """Let the claim into the answerable memory, and record who said so.

        The quarantine reason is deliberately left in place. Releasing a claim
        does not retract the judgement that held it; it adds the operator's own,
        and the two together are what a reader of the record needs.
        """
        return replace(self, trust=Trust.TRUSTED, release_note=note)

    def is_quarantined(self) -> bool:
        return self.trust == Trust.QUARANTINED

    def was_released(self) -> bool:
        return self.release_note is not None and not self.is_quarantined()

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
            "release_note": self.release_note,
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
            release_note=payload.get("release_note"),
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
class Corroboration:
    """How much of a claim's apparent agreement is somebody other than itself.

    A count of claims is not a count of sources. One rumour written down four
    times is four claims and one source, and a verdict that reports only "3
    supporting claims" has dressed a single voice up as a chorus. So the figure
    is broken out by the source class that made each agreeing claim, and three
    things are said about it:

    - ``classes`` — every agreeing claim, grouped by source class, highest
      entitled class first. What the raw count always was, still visible.
    - ``independent_classes`` — the classes *other than this claim's own*. This is
      the part that is actually corroboration.
    - ``echoes`` — agreeing claims from this claim's own class. Not support: the
      same source saying the same thing again is the claim talking to itself.

    A claim with echoes and no independent class is corroborated only by itself,
    and says so here rather than leaving the reader to divide one number by
    another to discover it.
    """

    classes: tuple[tuple[str, int], ...] = ()
    """Source class -> agreeing claims, including this one in its own class."""

    independent_classes: tuple[str, ...] = ()
    """Source classes other than this claim's own, highest entitled first."""

    echoes: int = 0
    """Agreeing claims from this claim's own source class."""

    @property
    def voices(self) -> int:
        """Distinct sources behind the claim, counting the claim itself once.

        Three people of the same class agreeing is one voice, not three. Reported
        next to :attr:`classes` rather than in place of it, so nothing is hidden.
        """
        return 1 + len(self.independent_classes)

    @property
    def self_corroborated(self) -> bool:
        """True when the claim is echoed by its own class and by nobody else."""
        return self.echoes > 0 and not self.independent_classes

    @property
    def agreeing_claims(self) -> int:
        """Every claim that asserts the same thing, echoes included."""
        return sum(count for _, count in self.classes)

    def count_for(self, source_class: str) -> int:
        return dict(self.classes).get(source_class, 0)

    def render(self) -> str:
        breakdown = ", ".join(f"{name} {count}" for name, count in self.classes)
        if self.self_corroborated:
            return (
                f"{breakdown} — one source class repeating itself, "
                "corroborated by no other source"
            )
        others = len(self.independent_classes)
        return f"{breakdown} — {others} other source class(es) agree"

    def to_payload(self) -> dict[str, Any]:
        return {
            "classes": [list(pair) for pair in self.classes],
            "independent_classes": list(self.independent_classes),
            "echoes": self.echoes,
            "voices": self.voices,
            "self_corroborated": self.self_corroborated,
        }


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
    """How many claims, this one included, assert the same thing.

    A claim count, not a source count: echoes are included, because they are real
    claims. Read :attr:`corroboration` to see how many of them are somebody other
    than this claim's own source class.
    """

    stale: bool = False
    corroboration: Corroboration | None = None
    """Who backs this claim, broken out by source class.

    Optional so a citation built without a population to compare against still
    renders. Never ``None`` on a claim a verdict used: the engine always knows
    what else on the device asserts the same thing, and declining to say would
    leave a reader to assume the number above was the whole story.
    """

    paths: tuple[str, ...] = ()
    """Which retrieval paths returned this claim: ``dense``, ``keyword``, or both.

    Measured by running each leg of the query on its own and attributing the
    claim to the legs that produced it. Empty only on a claim built outside a
    retrieval — a conflict side assembled by hand, say — where there is no query
    to attribute it to.
    """

    @staticmethod
    def of(
        claim: Claim,
        residency: ResidencyReason,
        corroboration: Corroboration | None = None,
        paths: tuple[str, ...] = (),
    ) -> "CitedClaim":
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
            corroborations=(
                corroboration.agreeing_claims if corroboration else 1
            ),
            corroboration=corroboration,
            paths=tuple(paths),
        )

    def to_payload(self) -> dict[str, Any]:
        """Plain data, so a verdict survives ``json.dumps`` intact.

        Built field by field rather than from ``vars()``: the corroboration is a
        value object, and a serialiser that reaches into ``__dict__`` would hand a
        caller something it cannot encode or compare.
        """
        return {
            "claim_id": self.claim_id,
            "subject": self.subject,
            "attribute": self.attribute,
            "value": self.value,
            "author": self.author,
            "observer": self.observer,
            "device_id": self.device_id,
            "observed_at": self.observed_at,
            "source_class": self.source_class,
            "trust": self.trust,
            "residency": self.residency,
            "reason": self.reason,
            "corroborations": self.corroborations,
            "stale": self.stale,
            "paths": list(self.paths),
            "corroboration": (
                self.corroboration.to_payload() if self.corroboration else None
            ),
        }


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
    """Claims on this device asserting the same value, this one included.

    The claim count, unchanged and still the raw figure. ``corroboration`` says
    how many of those are independent, which is the number that decides whether
    agreement is real.
    """

    entitling: bool
    corroboration: Corroboration | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "claim": self.claim.to_payload(),
            "supporters": self.supporters,
            "entitling": self.entitling,
            "corroboration": (
                self.corroboration.to_payload() if self.corroboration else None
            ),
        }


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
class PendingCorrection:
    """An answer this device has already given that no longer reflects its memory.

    **A notice, not an answer.** It names a question, the summary the device
    stood behind last time it was asked, and the claims that have since arrived
    about the same subject and aspect. It deliberately carries no new answer and
    no verdict kind, because producing one would mean answering a question nobody
    asked — and an operator who was handed a CORRECTED verdict they did not
    request could not tell the difference between the device noticing something
    and the device having been asked twice.

    The entry disappears on its own once the question is asked again: the claims
    it named are then behind the recorded answer, so nothing is outstanding. No
    acknowledgement call is needed, and none is possible to forget.
    """

    subject: str
    question: str
    previous_summary: str
    """The answer the device gave last time this question was asked."""

    claim_ids: tuple[str, ...]
    """Claims taken in since that answer, which are not behind it."""

    def to_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "question": self.question,
            "previous_summary": self.previous_summary,
            "claim_ids": list(self.claim_ids),
        }

    def render(self) -> str:
        arrivals = ", ".join(cid[:8] for cid in self.claim_ids)
        return (
            f"the answer already given about {self.subject} is out of date: "
            f"{len(self.claim_ids)} claim(s) about it have arrived since "
            f"({arrivals})"
        )


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
    """Measured off the clock around this question, never modelled."""

    paths_used: tuple[str, ...] = ()
    """The retrieval paths this question ran: ``dense`` and ``keyword``, or
    whichever one was run alone.

    A statement about the query, not about its results — a question that matched
    nothing still ran both paths. Which *claims* came from which path is on each
    citation, as :attr:`CitedClaim.paths`.
    """
    bytes_withheld: int = 0
    citation: str = ""
    """The provision this record is being kept under.

    Supplied by the bound vertical. The engine has no opinion about which
    regulation governs somebody's work, so an unbound device says so rather than
    borrowing another vertical's.
    """

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject": self.subject,
            "question": self.question,
            "summary": self.summary,
            "claims": [c.to_payload() for c in self.claims],
            "conflicts": [
                {
                    "a": a.claim.to_payload(),
                    "a_supporters": a.supporters,
                    "a_entitling": a.entitling,
                    "b": b.claim.to_payload(),
                    "b_supporters": b.supporters,
                    "b_entitling": b.entitling,
                    "a_corroboration": (
                        a.corroboration.to_payload() if a.corroboration else None
                    ),
                    "b_corroboration": (
                        b.corroboration.to_payload() if b.corroboration else None
                    ),
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
            "citation": self.citation,
        }
