"""The question interface: the one seam.

Asking a question about a subject returns exactly one `Verdict`, of exactly one
kind. The shape does not vary with the kind, so a caller never branches on which
fields exist â€” and the interface renders every verdict without needing to know
anything the verdict does not already carry.

Four verdicts, and the engine chooses between them:

- **ANSWERED_LOCALLY** â€” the device's own memory settled it. It says which
  claims it used and why each was permitted to be used.
- **CONFLICTED** â€” two claims about the same subject cannot both be true. Both
  are returned. The engine escalates and resolves nothing.
- **UNRESOLVED_CLOUD_REQUIRED** â€” the answer exists but sits at the depot, and
  the device was not permitted to send or receive it. It names what it needs
  and what that would cost.
- **CORRECTED** â€” the same question now answers differently because the device
  learned something. Both answers are returned.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from edgemem.domain import (
    AuthorityClass,
    AuthorityView,
    CausalContext,
    CitedClaim,
    Claim,
    ConflictSide,
    NeededClaim,
    Residency,
    ResidencyReason,
    Trust,
    Verdict,
    VerdictKind,
    utcnow,
)
from edgemem.residency import (
    DEFAULT_POLICY,
    ResidencyContext,
    ResidencyPolicy,
    estimate_outbound_bytes,
)
from edgemem.store import ShardStore


def default_ladder() -> tuple[tuple[AuthorityClass, int], ...]:
    """Source classes in descending order of entitlement to be believed.

    Data, not code. A deployment replaces it; the engine does not change.
    Recency is not a member of this ordering and is not consulted.
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
    """A versioned ordering of who could settle a conflict."""

    version: str = "v1"
    entries: tuple[tuple[AuthorityClass, int], ...] = default_ladder()

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
            note=(
                "Presented for a person to settle. The engine does not choose "
                "between disagreeing claims, and recency is not an input."
            ),
        )


@dataclass
class AnswerLog:
    """What the device believed last time it was asked.

    What makes CORRECTED possible: the earlier answer is retained, so the
    device can show that its belief changed rather than assert that it did.
    """

    entries: dict[tuple[str, str], str] = field(default_factory=dict)

    def previous(self, subject: str, question: str) -> str | None:
        return self.entries.get((subject, question.strip().lower()))

    def record(self, subject: str, question: str, summary: str) -> None:
        self.entries[(subject, question.strip().lower())] = summary

    def changed_by(self, subject: str, question: str) -> str | None:
        """The claim that most plausibly accounts for a changed answer."""
        return None


class EdgeMemory:
    """The device. Everything the engine can do, reached through `ask`."""

    def __init__(
        self,
        store: ShardStore,
        policy: ResidencyPolicy | None = None,
        ladder: Ladder | None = None,
        byte_budget: int = 8 * 1024 * 1024,
        quorum: int = 1,
    ) -> None:
        self.store = store
        self.policy = policy or DEFAULT_POLICY
        self.ladder = ladder or Ladder()
        self.byte_budget = byte_budget
        self.quorum = quorum
        self.log = AnswerLog()
        self._context_note: dict[str, ResidencyReason] = {}
        self._depot_known: set[str] = set()

    # -- recording ---------------------------------------------------------

    def record(self, claim: Claim) -> Claim:
        """Store a claim. Its residency is classified on demand, not cached.

        Caching at record time would freeze a decision the moment the claim
        lands, and a decision depends on the device's circumstances — whether
        the depot already holds it, how much allowance is left — which change
        afterwards. A cached reason could contradict the state it describes.
        """
        self.store.upsert([claim])
        self._context_note.pop(claim.claim_id, None)
        return claim

    def record_many(self, claims: Iterable[Claim]) -> list[Claim]:
        claims = list(claims)
        for c in claims:
            self.record(c)
        return claims

    def mark_present_at_depot(self, claim_ids: Iterable[str]) -> None:
        """Note claims the device's own record shows are already at the depot.

        Invalidates any cached classification: these claims just became
        CLOUD_ONLY-eligible, and a stale reason would still say otherwise.
        """
        self._depot_known.update(claim_ids)
        for cid in claim_ids:
            self._context_note.pop(cid, None)

    def residency_of(self, claim: Claim) -> ResidencyReason:
        return self._context_note.get(claim.claim_id) or self._reason_for(claim)

    def _reason_for(self, claim: Claim) -> ResidencyReason:
        held = self.store.claims_for(claim.subject)
        redundancy = 0.0
        if held:
            same = [
                c
                for c in held
                if c.attribute == claim.attribute and c.claim_id != claim.claim_id
            ]
            redundancy = len(same) / max(1, len(held))
        return self.policy.decide(
            claim,
            ResidencyContext(
                decided_at=utcnow(),
                byte_budget=self.byte_budget,
                remaining_bytes=self.byte_budget,
                redundancy=redundancy,
                depot_copies=1 if claim.claim_id in self._depot_known else 0,
            ),
        )

    # -- the seam ----------------------------------------------------------

    def ask(self, question: str, subject: str | None = None) -> Verdict:
        """The one interface. Returns exactly one labelled verdict."""
        started = time.perf_counter()
        question = question.strip()
        subject = subject or self._infer_subject(question)

        if subject is None:
            return Verdict(
                kind=VerdictKind.UNRESOLVED_CLOUD_REQUIRED,
                subject="",
                question=question,
                summary=(
                    "No claim on this device concerns that subject, so the "
                    "device cannot answer from its own memory."
                ),
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        rows, elapsed = self.store.search(question, limit=8)
        latency = (time.perf_counter() - started) * 1000
        paths = tuple(sorted({p for _, _, p in rows}))

        # An answer must be about the attribute being asked for. A claim about
        # the trailer's temperature is not an answer to a question about its
        # seal, and treating it as one is the confident-wrong-answer failure the
        # device exists to avoid.
        attribute = self._attribute_for(question, subject)
        usable = [c for c, _, _ in rows if c.subject == subject]
        if attribute is not None:
            usable = [c for c in usable if c.attribute == attribute]
        usable = [c for c in usable if self._is_answerable(c)]
        withheld = self._withheld_for(subject)
        conflicts = self._conflicts_for(subject)
        cited = tuple(
            CitedClaim.of(c, self.residency_of(c)) for c in usable
        )
        withheld_bytes = sum(estimate_outbound_bytes(c) for c in withheld)

        if conflicts:
            return self._conflicted(
                subject, question, conflicts, cited, latency, paths, withheld_bytes
            )
        if cited:
            verdict = Verdict(
                kind=VerdictKind.ANSWERED_LOCALLY,
                subject=subject,
                question=question,
                summary=self._summarise(cited),
                claims=cited,
                needed=tuple(
                    NeededClaim(
                        claim_id=c.claim_id,
                        subject=c.subject,
                        attribute=c.attribute,
                        value=c.value,
                        why_withheld=self._why_withheld(c),
                        bytes_if_sent=estimate_outbound_bytes(c),
                        device_id=c.device_id,
                    )
                    for c in withheld
                ),
                latency_ms=latency,
                paths_used=paths,
                bytes_withheld=withheld_bytes,
            )
            previous = self.log.previous(subject, question)
            if previous is not None and previous != verdict.summary:
                verdict = Verdict(
                    kind=VerdictKind.CORRECTED,
                    subject=subject,
                    question=question,
                    summary=verdict.summary,
                    claims=cited,
                    needed=verdict.needed,
                    previous_summary=previous,
                    changed_by=cited[0].claim_id,
                    latency_ms=latency,
                    paths_used=paths,
                    bytes_withheld=withheld_bytes,
                )
            self.log.record(subject, question, verdict.summary)
            return verdict

        return Verdict(
            kind=VerdictKind.UNRESOLVED_CLOUD_REQUIRED,
            subject=subject,
            question=question,
            summary=(
                f"The device holds no usable claim about {subject}."
                + (
                    f" It holds {len(withheld)} that it was not permitted to use."
                    if withheld
                    else ""
                )
            ),
            needed=tuple(
                NeededClaim(
                    claim_id=c.claim_id,
                    subject=c.subject,
                    attribute=c.attribute,
                    value=c.value,
                    why_withheld=self._why_withheld(c),
                    bytes_if_sent=estimate_outbound_bytes(c),
                    device_id=c.device_id,
                )
                for c in withheld
            ),
            latency_ms=latency,
            paths_used=paths,
            bytes_withheld=withheld_bytes,
        )

    # -- verdict construction ---------------------------------------------

    def _conflicted(
        self,
        subject: str,
        question: str,
        conflicts: list[tuple[Claim, Claim]],
        cited: tuple[CitedClaim, ...],
        latency: float,
        paths: tuple[str, ...],
        withheld_bytes: int,
    ) -> Verdict:
        sides: list[ConflictSide] = []
        for left, right in conflicts:
            a = CitedClaim.of(left, self.residency_of(left))
            b = CitedClaim.of(right, self.residency_of(right))
            a_rank = self.ladder.rank(left.source_class)
            b_rank = self.ladder.rank(right.source_class)
            a_supporters = self._supporters(left)
            b_supporters = self._supporters(right)
            sides.append(
                ConflictSide(
                    claim=a,
                    supporters=a_supporters,
                    entitling=a_rank >= b_rank,
                )
            )
            sides.append(
                ConflictSide(
                    claim=b,
                    supporters=b_supporters,
                    entitling=b_rank > a_rank,
                )
            )

        all_claims = [c for pair in conflicts for c in pair]
        return Verdict(
            kind=VerdictKind.CONFLICTED,
            subject=subject,
            question=question,
            summary=(
                f"{len(conflicts)} disagreement(s) about {subject}. Every side "
                "is retained. The engine is not choosing between them."
            ),
            claims=cited,
            conflicts=tuple(
                (sides[i], sides[i + 1]) for i in range(0, len(sides), 2)
            ),
            authority=self.ladder.view(all_claims),
            latency_ms=latency,
            paths_used=paths,
            bytes_withheld=withheld_bytes,
        )

    def _supporters(self, claim: Claim) -> int:
        """How many independent claims on this device agree with this one."""
        same = [
            c
            for c in self.store.claims_for(claim.subject)
            if c.attribute == claim.attribute and c.value == claim.value
        ]
        return len(same)

    def _conflicts_for(self, subject: str) -> list[tuple[Claim, Claim]]:
        """Pairs of claims that cannot both be true, and are concurrent.

        Concurrency is causal, not chronological: a later claim that saw the
        earlier one is a revision, not a disagreement.
        """
        rows = [
            c
            for c in self.store.claims_for(subject)
            if not c.is_quarantined()
        ]
        pairs: list[tuple[Claim, Claim]] = []
        seen: set[tuple[str, str]] = set()
        for i, left in enumerate(rows):
            for right in rows[i + 1 :]:
                if left.attribute != right.attribute:
                    continue
                if left.value == right.value:
                    continue
                if not left.causal.is_concurrent_with(right.causal):
                    continue
                key = tuple(sorted((left.claim_id, right.claim_id)))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append((left, right))
        return pairs

    def _attribute_for(self, question: str, subject: str) -> str | None:
        """Which attribute the question is asking about, or None if unclear.

        Taken from the attributes the device actually holds for this subject,
        ranked by how well they match the question. Returns None when no
        attribute stands out, in which case the answer is not narrowed — a
        broad question legitimately spans several attributes.
        """
        held = self.store.claims_for(subject)
        if not held:
            return None
        attributes = {c.attribute for c in held}
        if len(attributes) == 1:
            return next(iter(attributes))
        lowered = question.lower()
        best: str | None = None
        best_hits = 0
        for attr in attributes:
            hits = sum(1 for tok in attr.lower().split() if tok in lowered)
            if hits > best_hits:
                best_hits, best = hits, attr
        return best if best_hits else None

    def _is_answerable(self, claim: Claim) -> bool:
        return not claim.is_quarantined() and (
            self.residency_of(claim).residency is not Residency.CLOUD_ONLY
        )

    def _answerable_for(self, subject: str) -> list[Claim]:
        """Claims about this subject the device can actually answer from.

        Excludes quarantined claims and CLOUD_ONLY claims: the first because
        the device does not trust them, the second because it holds only a
        pointer to a record that lives at the depot. A LOCAL claim is
        answerable â€” that is what staying local means.
        """
        return [
            c for c in self.store.claims_for(subject) if self._is_answerable(c)
        ]

    def _withheld_for(self, subject: str) -> list[Claim]:
        """Claims about this subject the device cannot use for an answer.

        A LOCAL claim is *not* withheld: staying on the device is the normal
        case, and such a claim is answerable from memory precisely because it
        never left. A claim is withheld when it is quarantined, or when the
        device holds it only as a pointer to something already at the depot.
        """
        return [
            c
            for c in self.store.claims_for(subject)
            if c.is_quarantined()
            or self.residency_of(c).residency is Residency.CLOUD_ONLY
        ]

    def _why_withheld(self, claim: Claim) -> str:
        """Why this claim is not answering, in the caller's words.

        Names the quarantine when that is the cause, and the residency reason
        otherwise, so the missing claim carries its own explanation.
        """
        if claim.is_quarantined():
            return f"quarantined: {claim.quarantine_reason}"
        return self.residency_of(claim).render()

    def _summarise(self, cited: tuple[CitedClaim, ...]) -> str:
        parts = [f"{c.attribute} is recorded as {c.value!r}" for c in cited[:3]]
        if len(cited) > 3:
            parts.append(f"(+{len(cited) - 3} more)")
        return "; ".join(parts)

    def _infer_subject(self, question: str) -> str | None:
        """Find which subject the question is about, from what the device holds.

        Deliberately conservative: it only offers a subject it has actually seen
        ranked against the question, and returns None when nothing stands out.
        """
        rows, _ = self.store.search(question, limit=10)
        if not rows:
            return None
        return rows[0][0].subject

    # -- inspection --------------------------------------------------------

    def memory(self) -> list[Claim]:
        return self.store.all_claims()

    def index_state(self) -> str:
        return self.store.index_state().describe()

    def stats(self) -> dict:
        claims = self.memory()
        by_residency: dict[str, int] = {}
        for c in claims:
            r = self.residency_of(c).residency.value
            by_residency[r] = by_residency.get(r, 0) + 1
        return {
            "claims": len(claims),
            "by_residency": by_residency,
            "quarantined": sum(1 for c in claims if c.is_quarantined()),
            "index": self.index_state(),
            "ladder_version": self.ladder.version,
        }
