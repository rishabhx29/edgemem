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

Two further things every verdict says about the claims it leans on, both because
a bare count is not the same as agreement: where each claim came from, and how
much of the apparent backing for it comes from a source other than its own. A
rumour written down four times is one voice that got louder, and a verdict that
reported only "three supporting claims" would have called it a chorus.

And one thing the device notices without being asked: when a claim arrives that
makes an answer it has already given stale, ``pending_corrections()`` says so. It
is a notice and not an answer — no verdict, no summary, no query — because an
operator handed a CORRECTED verdict they had not requested could not tell the
device's awareness apart from the seam having been used twice.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

from edgemem.domain import (
    AuthorityClass,
    CausalContext,
    CitedClaim,
    Claim,
    ConflictSide,
    Corroboration,
    Ladder,
    NeededClaim,
    PendingCorrection,
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
from edgemem.trust import DEFAULT_TRUST_POLICY, TrustPolicy

__all__ = [
    "AnswerLog",
    "EdgeMemory",
    "Ladder",
    "PendingCorrection",
    "TrustPolicy",
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
    _unanswered: set[tuple[str, str]] = field(default_factory=set)
    _seen_claims: set[str] = field(default_factory=set)

    def previous(self, subject: str, question: str) -> str | None:
        return self.entries.get((subject, question.strip().lower()))

    def mark_unanswered(self, subject: str, question: str, summary: str) -> None:
        """Record that the device could not answer this at all, and what it said.

        So that learning enough to answer later is reportable as a correction,
        rather than as a first answer that happens to be confident.

        The summary is kept, not just the fact. An unanswered question is the one
        case where the device still had something to say, and storing an empty
        string in its place meant the CORRECTED verdict that followed could report
        that it had changed without being able to show what it had changed from —
        the interface promised both answers and had only one to hand.
        """
        key = (subject, question.strip().lower())
        self._unanswered.add(key)
        self.entries.setdefault(key, summary)

    def was_unresolved(self, subject: str, question: str) -> bool:
        return (subject, question.strip().lower()) in self._unanswered

    def learned_since_last_ask(self, subject: str, question: str) -> bool:
        """True when a claim this device had not seen is now backing the answer.

        The transition that matters: the device could not answer, and now can.
        Being able to answer is not news; becoming able to answer is.
        """
        key = (subject, question.strip().lower())
        cited = self._cited.get(key, frozenset())
        return bool(cited - self._seen_claims)

    def note_known(self, claim_ids: Iterable[str]) -> None:
        """Record which claims the device already had, before answering."""
        self._seen_claims.update(claim_ids)

    def clear_unanswered(self, subject: str, question: str) -> None:
        """The device can answer this now, so it is no longer outstanding."""
        self._unanswered.discard((subject, question.strip().lower()))

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
        trust: TrustPolicy | None = None,
    ) -> None:
        self.store = store
        self.device_id = store.device_id
        self.pack = pack or DEFAULT_PACK
        self.policy = policy or DEFAULT_POLICY
        # An explicitly supplied ladder outranks the pack's, so an operator can
        # tighten the ordering for one device without writing a new vertical.
        self.ladder = ladder or self.pack.ladder
        # Likewise the trust gate: the pack names who outranks whom, and never
        # which sources this device refuses. An operator who distrusts a class
        # says so here, and the device's memory is arranged accordingly.
        self.trust = trust or DEFAULT_TRUST_POLICY
        self.byte_budget = byte_budget
        self.quorum = quorum
        self.outbox = outbox
        self.log = AnswerLog()
        self._context_note: dict[str, ResidencyReason] = {}
        self._depot_known: set[str] = set()
        # Claims this device has taken in — recorded here, or absorbed from the
        # depot — and which some answer it has already given does not yet stand
        # behind. See :meth:`pending_corrections`.
        self._taken_in: set[str] = set()
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

        The claim the device keeps is the one the trust gate returned, not the one
        that was handed over. A claim from a held source is stored apart rather
        than refused: it is still on the device, still readable, still the
        operator's to look at — and it cannot answer anything.
        """
        claim = self._gate(claim)
        with self.hold_writes():
            if self.outbox is not None:
                self.outbox.enqueue(claim)
            self.store.upsert([claim])
        self._context_note.pop(claim.claim_id, None)
        self._taken_in.add(claim.claim_id)
        self._writes += 1
        return claim

    def record_many(self, claims: Iterable[Claim]) -> list[Claim]:
        return [self.record(c) for c in claims]

    def _gate(self, claim: Claim) -> Claim:
        """The claim as this device will hold it, after the trust gate.

        Judged once, on arrival, and only ever to add a quarantine: a claim that
        already carries one keeps its author's reason, because the gate has no
        standing to overrule a judgement that was made on purpose. Nothing here
        reads a clock or consults anything but the ladder, so the same claim
        reaching two devices reaches the same verdict — and neither the arrival
        nor any later moment can undo it.
        """
        if claim.is_quarantined():
            return claim
        decision = self.trust.decide(claim, self.ladder)
        if not decision.held:
            return claim
        return claim.with_trust(decision.trust, decision.reason)

    def release(self, claim_ids: Iterable[str], reason: str) -> list[Claim]:
        """Let held claims into the answerable memory, and say who let them.

        An action, not a state. Nothing else in the engine can end a quarantine:
        the claim is not re-judged as time passes, the shard is not re-judged when
        it is reopened, and a reconnect replays the decision that was already made
        rather than making a new one. Somebody has to do this, and ``reason`` is
        required because a release nobody can account for is indistinguishable
        from a quarantine that quietly lapsed.

        Returns the claims that were actually released. A claim that was never
        held is left alone and simply does not appear: the caller asked to be
        told what it got, and a name the device does not hold raises instead,
        because that is a caller mistake rather than a judgement.
        """
        if not reason or not reason.strip():
            raise ValueError("a release must say who is releasing the claim and why")
        released: list[Claim] = []
        for claim_id in claim_ids:
            held = self.store.get(claim_id)
            if held is None:
                raise KeyError(f"this device holds no claim {claim_id!r} to release")
            if not held.is_quarantined():
                continue
            let_go = held.with_release(reason.strip())
            with self.hold_writes():
                self.store.upsert([let_go])
            self._context_note.pop(let_go.claim_id, None)
            released.append(let_go)
        return released

    def absorb(self, claims: Iterable[Claim]) -> list[Claim]:
        """Take claims as they arrive from the depot.

        Stored as they were asserted: claim id, author, observer, the moment the
        observation was perceived, and the causal context all travel unchanged. A
        claim re-stamped as this device's own observation would be a different
        claim. The conflict register decides what is concurrent from that
        context, so an incoming assertion dressed as a local one would be
        indistinguishable from something this device actually saw, which is
        precisely the confusion the register exists to prevent.

        The trust gate does mark arriving claims, and that is not a contradiction
        of the above: provenance is untouched, and being unable to believe a
        source is a separate fact from where the assertion came from. A claim the
        gate holds is kept whole and readable, held apart from what answers.

        Not queued for onward transmission. These claims came from the depot, and
        the depot already holds them.

        A claim the depot holds is not marked cloud-only either. This device holds
        a readable copy and has no cloud read path to fall back on, so marking it
        would convert a working answer into a refusal. :meth:`mark_present_at_depot`
        stays the caller's explicit decision about whether the local copy is still
        the one to answer from.
        """
        claims = [self._gate(c) for c in claims]
        if not claims:
            return []
        with self.hold_writes():
            self.store.upsert(claims)
        for claim in claims:
            self._context_note.pop(claim.claim_id, None)
            self._taken_in.add(claim.claim_id)
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

        found = self.store.traced_search(question, limit=8)
        latency = (time.perf_counter() - started) * 1000
        paths = found.paths
        # Which leg returned which claim. Measured by the store, not inferred from
        # the fused score, so a citation can say that a claim surfaced on the
        # keyword leg alone rather than only that a hybrid query ran.
        by_claim = {hit.claim.claim_id: hit.legs for hit in found.hits}

        # An answer must be about the attribute being asked for. A claim about
        # one aspect of a subject is not an answer to a question about another,
        # and treating it as one is the confident-wrong-answer failure the
        # device exists to avoid.
        attribute = self._attribute_for(question, subject)
        usable = [hit.claim for hit in found.hits if hit.claim.subject == subject]
        if attribute is not None:
            usable = [c for c in usable if c.attribute == attribute]
        usable = [c for c in usable if self._is_answerable(c)]
        withheld = self._withheld_for(subject)
        conflicts = self._conflicts_for(subject)
        # One read of the corroboration population per asserted value, shared by
        # every claim that asserts it, so the figure a verdict reports is the
        # figure the engine measured rather than one recomputed per citation.
        agreeing: dict[str, list[Claim]] = {}
        for candidate in usable:
            agreeing.setdefault(
                _agreement_key(candidate), self._agreements(candidate)
            )
        cited = tuple(
            CitedClaim.of(
                c,
                self.residency_of(c),
                self._corroboration(c, agreeing[_agreement_key(c)]),
                paths=by_claim.get(c.claim_id, ()),
            )
            for c in usable
        )
        withheld_bytes = sum(estimate_outbound_bytes(c) for c in withheld)

        if conflicts:
            return self._conflicted(
                subject,
                question,
                conflicts,
                cited,
                latency,
                paths,
                withheld_bytes,
                by_claim,
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
            was_unresolved = self.log.was_unresolved(subject, question)
            if (previous is not None and previous != verdict.summary) or (
                was_unresolved and self.log.learned_since_last_ask(subject, question)
            ):
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
            self.log.clear_unanswered(subject, question)
            return verdict

        unresolved = self._unresolved(
            subject, question, withheld, latency, paths, withheld_bytes
        )
        self.log.mark_unanswered(subject, question, unresolved.summary)
        return unresolved

    def _unresolved(
        self,
        subject: str,
        question: str,
        withheld: list[Claim],
        latency: float,
        paths: tuple[str, ...],
        withheld_bytes: int,
    ) -> Verdict:
        """What the device says when it holds nothing it is permitted to use.

        The claim is that it cannot answer, which is a claim about what is missing
        rather than about the world, so everything it can still say is said here:
        the count it is withholding and the byte cost of being allowed to.
        """
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
        by_claim: dict[str, tuple[str, ...]] | None = None,
    ) -> Verdict:
        by_claim = by_claim or {}
        sides: list[ConflictSide] = []
        for left, right in conflicts:
            left_agreeing = self._agreements(left)
            right_agreeing = self._agreements(right)
            a = CitedClaim.of(
                left,
                self.residency_of(left),
                self._corroboration(left, left_agreeing),
                paths=by_claim.get(left.claim_id, ()),
            )
            b = CitedClaim.of(
                right,
                self.residency_of(right),
                self._corroboration(right, right_agreeing),
                paths=by_claim.get(right.claim_id, ()),
            )
            a_rank = self.ladder.rank(left.source_class)
            b_rank = self.ladder.rank(right.source_class)
            a_supporters = len(left_agreeing)
            b_supporters = len(right_agreeing)
            sides.append(
                ConflictSide(
                    claim=a,
                    supporters=a_supporters,
                    entitling=a_rank >= b_rank,
                    corroboration=a.corroboration,
                )
            )
            sides.append(
                ConflictSide(
                    claim=b,
                    supporters=b_supporters,
                    entitling=b_rank > a_rank,
                    corroboration=b.corroboration,
                )
            )

        all_claims = [c for pair in conflicts for c in pair]
        self_corroborated = [
            side
            for side in sides
            if side.corroboration is not None and side.corroboration.self_corroborated
        ]
        echo_note = (
            f" {len(self_corroborated)} of these sides are corroborated only by "
            "their own source class, which is that source agreeing with itself."
            if self_corroborated
            else ""
        )
        return Verdict(
            kind=VerdictKind.CONFLICTED,
            subject=subject,
            question=question,
            summary=(
                f"{len(conflicts)} {self.pack.labels.conflict}(s) about "
                f"{subject}. Every side is retained. The engine is not choosing "
                f"between them.{echo_note}"
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

    def _agreements(self, claim: Claim) -> list[Claim]:
        """Everything this device holds that asserts the same thing.

        This is the raw claim count, echoes and all: it answers how much is
        written down, which is not the same question as :meth:`_corroboration`'s
        how much of it is somebody else. Both figures reach the caller, rather
        than one of them quietly standing in for the other.

        Quarantined claims are not in this population. They are still readable
        and still stored, but a claim the device has declined to believe cannot be
        turned into evidence for one it has accepted — otherwise the gate could be
        walked around by writing the same rumour twice.

        Residency is not consulted, and deliberately so. A claim that lives at the
        depot and a claim that never left are both still assertions this device
        can read, and both may corroborate. Where a claim may *live* and whether
        it may be *believed* are separate questions, and only one of them is a
        judgement about the claim's source.
        """
        return [
            c
            for c in self.store.claims_for(claim.subject)
            if c.attribute == claim.attribute
            and c.value == claim.value
            and not c.is_quarantined()
        ]

    def _corroboration(self, claim: Claim, agreeing: list[Claim] | None = None) -> Corroboration:
        """How many distinct sources back this claim, and who they are.

        A claim corroborated only by its own source class has not been corroborated
        at all, and saying so is the whole point of counting per class rather than
        counting claims: a rumour the same rumour keeps repeating is one voice
        that got louder, and a bare integer would report it as a chorus.

        Classes are reported in ladder order, highest entitled first, so the
        figure reads in the order a reader would weigh it. Recency plays no part:
        the newest echo is no more corroboration than the oldest.
        """
        agreeing = self._agreements(claim) if agreeing is None else agreeing
        by_class: dict[str, int] = {}
        for other in agreeing:
            key = other.source_class.value
            by_class[key] = by_class.get(key, 0) + 1
        ordered = sorted(
            by_class,
            key=lambda name: (
                -self.ladder.rank(AuthorityClass(name)),
                name,
            ),
        )
        own = claim.source_class.value
        return Corroboration(
            classes=tuple((name, by_class[name]) for name in ordered),
            independent_classes=tuple(name for name in ordered if name != own),
            echoes=by_class.get(own, 1) - 1,
        )

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
        echoes = [
            c
            for c in cited
            if c.corroboration is not None and c.corroboration.self_corroborated
        ]
        for c in echoes:
            # Said here, in the sentence the caller reads, rather than left to be
            # recovered by dividing one number by another. A rumour the rumour
            # keeps repeating is the failure this exists to catch, and a warning
            # nobody has to look for is not a warning.
            parts.append(
                f"no source class other than {c.source_class} agrees with it "
                f"({c.corroboration.echoes} further "
                f"{c.source_class} claim(s) repeat it)"
            )
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

    def pending_corrections(self) -> tuple[PendingCorrection, ...]:
        """Answers this device has already given that its memory has moved past.

        **Why this exists.** A CORRECTED verdict can only be produced by comparing
        a question against a previous answer to the same question, so without this
        the operator who has to know would have to ask again in order to find out
        that asking again would have helped. That is the awareness the device has
        of its own staleness doing the operator's job for them.

        **What it does not do.** It does not ask anything. It reads the answer
        log, reads the claims on the shard, and reports the difference; it issues
        no retrieval query, renders no summary, and returns no verdict. A caller
        that has not asked is told that its answer is stale, not handed a new one,
        so nothing here can be mistaken for the seam having been used twice.

        **What counts as stale.** A claim the device took in — recorded here, or
        absorbed from the depot — that belongs to the same subject and the same
        aspect the question was narrowed to, that the device could answer from,
        and that is not already behind the recorded answer. The narrowing is the
        one :meth:`ask` applies, so a claim about another aspect does not make an
        answer stale.

        **How it clears.** Asking the question again records those claims behind
        the new answer and the entry disappears. There is no acknowledgement to
        forget and nothing to clear by hand.
        """
        outstanding: list[PendingCorrection] = []
        for (subject, question), summary in self.log.entries.items():
            attribute = self._attribute_for(question, subject)
            arrived = tuple(
                c.claim_id
                for c in self.store.claims_for(subject)
                if c.claim_id in self._taken_in
                and c.claim_id not in self.log.previous_cited(subject, question)
                and (attribute is None or c.attribute == attribute)
                and self._is_answerable(c)
            )
            if arrived:
                outstanding.append(
                    PendingCorrection(
                        subject=subject,
                        question=question,
                        previous_summary=summary,
                        claim_ids=arrived,
                    )
                )
        return tuple(outstanding)

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
            "released": sum(1 for c in claims if c.was_released()),
            "index": self.index_state(),
            "ladder_version": self.ladder.version,
            "trust_policy_version": self.trust.policy_version,
            "trust_held_classes": sorted(
                c.value for c in self.trust.held_classes
            ),
            "trust_entitlement_floor": self.trust.entitlement_floor,
            "pending_corrections": len(self.pending_corrections()),
            "answered_questions": len(self.log.entries),
        }


def _agreement_key(claim: Claim) -> tuple[str, str, str]:
    """What two claims must match to be corroborating each other.

    Subject, attribute and value. Not the source class — that is precisely what
    the corroboration figure is trying to work out — and not the author, because
    one person restating something is as much an echo as one class restating it.
    """
    return (claim.subject, claim.attribute, claim.value)
