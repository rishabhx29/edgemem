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

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

from edgemem.domain import (
    CausalContext,
    CitedClaim,
    Claim,
    ConflictSide,
    Ladder,
    NeededClaim,
    Residency,
    ResidencyReason,
    Trust,
    Verdict,
    VerdictKind,
    default_ladder,
    utcnow,
)
from edgemem.outbox import Outbox
from edgemem.residency import (
    DEFAULT_POLICY,
    ResidencyContext,
    ResidencyPolicy,
    estimate_outbound_bytes,
)
from edgemem.schema import DEFAULT_PACK, SchemaPack
from edgemem.store import ShardStore

__all__ = [
    "AnswerLog",
    "EdgeMemory",
    "Ladder",
    "default_ladder",
]
"""``Ladder`` and ``default_ladder`` are re-exported from here for callers that
have always reached the authority ordering through the question interface. They
are defined in :mod:`edgemem.domain` because a vertical must be able to name a
ladder without importing this module."""


@dataclass
class AnswerLog:
    """What the device believed last time it was asked.

    What makes CORRECTED possible: the earlier answer is retained, so the
    device can show that its belief changed rather than assert that it did.
    """

    entries: dict[tuple[str, str], str] = field(default_factory=dict)
    _cited: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)

    def previous(self, subject: str, question: str) -> str | None:
        return self.entries.get((subject, question.strip().lower()))

    def previous_cited(self, subject: str, question: str) -> frozenset[str]:
        """The claim ids behind the previous answer, or empty if there was none."""
        return self._cited.get((subject, question.strip().lower()), frozenset())

    def record(self, subject: str, question: str, summary: str, cited: Iterable[Claim]) -> None:
        key = (subject, question.strip().lower())
        self.entries[key] = summary
        self._cited[key] = frozenset(c.claim_id for c in cited)

    def changed_by(
        self, subject: str, question: str, current: Iterable[Claim]
    ) -> str | None:
        """The claim that changed the answer, or None if none accounts for it.

        The claim ids behind the previous answer are compared with the ones
        behind this one. Exactly one newcomer is a cause; zero or several is not
        something this log can attribute, and it says so rather than guessing.
        """
        newcomers = {c.claim_id for c in current} - self.previous_cited(
            subject, question
        )
        if len(newcomers) == 1:
            return next(iter(newcomers))
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
        pack: SchemaPack | None = None,
        outbox: Outbox | None = None,
    ) -> None:
        self.store = store
        self.device_id = store.device_id
        self.pack = pack or DEFAULT_PACK
        self.policy = policy or DEFAULT_POLICY
        # An explicitly supplied ladder outranks the pack's, so an operator can
        # tighten the ordering for one device without writing a new vertical.
        self.ladder = ladder or self.pack.ladder
        self.byte_budget = byte_budget
        self.quorum = quorum
        self.outbox = outbox
        self.log = AnswerLog()
        self._context_note: dict[str, ResidencyReason] = {}
        self._depot_known: set[str] = set()
        self._write_gate = threading.RLock()
        self._writes = 0
        self.recovered = self._recover()

    # -- recording ---------------------------------------------------------

    def record(self, claim: Claim) -> Claim:
        """Store a claim. Its residency is classified on demand, not cached.

        Caching at record time would freeze a decision the moment the claim
        lands, and a decision depends on the device's circumstances — whether
        the depot already holds it, how much allowance is left — which change
        afterwards. A cached reason could contradict the state it describes.

        The write-ahead entry is fsynced before the claim reaches the store,
        because that ordering is what the durability rests on: a claim that is in
        the queue survives a kill, and a claim sitting only in a buffered shard
        does not. What the queue holds is a transport intention rather than a
        residency verdict, so a claim the policy would never send waits in the
        queue for a sync to classify it and decline.
        """
        with self.hold_writes():
            if self.outbox is not None:
                self.outbox.enqueue(claim)
            self.store.upsert([claim])
        self._context_note.pop(claim.claim_id, None)
        self._writes += 1
        return claim

    def record_many(self, claims: Iterable[Claim]) -> list[Claim]:
        claims = list(claims)
        for c in claims:
            self.record(c)
        return claims

    def absorb(self, claims: Iterable[Claim]) -> list[Claim]:
        """Take claims as they arrive from the depot.

        Stored exactly as they were asserted: claim id, author, observer, the
        moment the observation was perceived, and the causal context all travel
        unchanged. A claim re-stamped as this device's own observation would be a
        different claim. The conflict register decides what is concurrent from
        that context, so an incoming assertion dressed as a local one would be
        indistinguishable from something this device actually saw, which is
        precisely the confusion the register exists to prevent.

        Not queued for onward transmission. These claims came from the depot, and
        the depot already holds them.

        A claim the depot holds is not marked cloud-only either. This device holds
        a readable copy and has no cloud read path to fall back on, so marking it
        would convert a working answer into a refusal. :meth:`mark_present_at_depot`
        stays the caller's explicit decision about whether the local copy is still
        the one to answer from.
        """
        claims = list(claims)
        if not claims:
            return []
        with self.hold_writes():
            self.store.upsert(claims)
        for claim in claims:
            self._context_note.pop(claim.claim_id, None)
        return claims

    def _recover(self) -> list[Claim]:
        """Put back what the write-ahead log knows about and memory does not.

        The outbox is fsynced before :meth:`record` returns, so a kill between the
        queue entry and the shard's own flush leaves a claim that is on disk and
        not yet in memory. Replaying the queue when the device is opened closes
        that window instead of leaving it to a caller to remember.
        """
        if self.outbox is None:
            return []
        outstanding = [
            claim
            for claim in self.outbox.pending()
            if self.store.get(claim.claim_id) is None
        ]
        if outstanding:
            self.store.upsert(outstanding)
        return outstanding

    def hold_writes(self) -> threading.RLock:
        """The write gate, held for as long as the caller needs it.

        Taken around every write and held across the application of an incoming
        page. A local record therefore either lands before the page or waits for
        it; it cannot interleave with it and be lost. Reads do not take the gate,
        so a sync never stalls the question interface.
        """
        return self._write_gate

    def local_writes(self) -> int:
        """Claims written on this device since it was opened.

        A count rather than a queue reading, so a sync can report how much local
        work happened underneath it without either end inspecting the other's
        storage.
        """
        return self._writes

    def flush(self) -> None:
        """Make the shard's buffered writes durable now.

        The outbox already covers this device's own claims before :meth:`record`
        returns. A side that others treat as authoritative about what it holds
        has no such cover -- the depot, before it numbers a claim in a sequence
        other devices will be waiting on -- and so calls this itself.
        """
        self.store.flush()

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
                    f"No {self.pack.labels.claim} on this device concerns any "
                    f"{self.pack.subject_model.id_label}, so the device cannot "
                    "answer from its own memory."
                ),
                citation=self.pack.citation,
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        rows, elapsed = self.store.search(question, limit=8)
        latency = (time.perf_counter() - started) * 1000
        paths = tuple(sorted({p for _, _, p in rows}))

        # An answer must be about the attribute being asked for. A claim about
        # one aspect of a subject is not an answer to a question about another,
        # and treating it as one is the confident-wrong-answer failure the
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
                citation=self.pack.citation,
            )
            previous = self.log.previous(subject, question)
            if previous is not None and previous != verdict.summary:
                # Compute the cause against the previous answer's claims before
                # recording this one, which is what overwrites them.
                cause = self.log.changed_by(subject, question, usable)
                verdict = Verdict(
                    kind=VerdictKind.CORRECTED,
                    subject=subject,
                    question=question,
                    summary=verdict.summary,
                    claims=cited,
                    needed=verdict.needed,
                    previous_summary=previous,
                    changed_by=cause,
                    latency_ms=latency,
                    paths_used=paths,
                    bytes_withheld=withheld_bytes,
                    citation=self.pack.citation,
                )
            self.log.record(subject, question, verdict.summary, usable)
            return verdict

        return Verdict(
            kind=VerdictKind.UNRESOLVED_CLOUD_REQUIRED,
            subject=subject,
            question=question,
            summary=(
                f"The device holds no usable {self.pack.labels.claim} about "
                f"{subject}."
                + (
                    f" It holds {len(withheld)} it was not permitted to use."
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
            citation=self.pack.citation,
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
                f"{len(conflicts)} {self.pack.labels.conflict}(s) about "
                f"{subject}. Every side is retained. The engine is not choosing "
                "between them."
            ),
            claims=cited,
            conflicts=tuple(
                (sides[i], sides[i + 1]) for i in range(0, len(sides), 2)
            ),
            authority=self.ladder.view(all_claims),
            latency_ms=latency,
            paths_used=paths,
            bytes_withheld=withheld_bytes,
            citation=self.pack.citation,
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

        The bound vertical's default attribute is the one exception: a vertical
        that says which aspect its operators ask about gets that aspect when
        the question is too vague to name one, because a bare question in that
        vertical is nearly always about it. With nothing bound there is no
        default and the answer stays unnarrowed.
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
        if best_hits:
            return best
        default = self.pack.subject_model.default_attribute
        return default if default in attributes else None

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

    def _attribute_label(self, attribute: str) -> str:
        """The vertical's name for an attribute, or the pack's generic word.

        A mapping lookup, not a branch: an attribute the vertical has not named
        is rendered with the pack's own word for the concept rather than with a
        raw key the caller would have to guess at.
        """
        model = self.pack.subject_model
        return model.attributes.get(attribute, self.pack.labels.attribute)

    def _summarise(self, cited: tuple[CitedClaim, ...]) -> str:
        parts = [
            f"{self._attribute_label(c.attribute)} is recorded as {c.value!r}"
            for c in cited[:3]
        ]
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
