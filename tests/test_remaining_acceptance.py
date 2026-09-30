"""Acceptance tests for the three remaining tickets: 03, 05 and 09.

Every checkbox on those three tickets is asserted here, once, by the observation
it actually calls for. Where a criterion was already met by an existing test that
is noted in the section heading rather than duplicated.

The device is driven the way a caller drives it — ``ask`` for anything a caller
would see, and the surfaces the engine already publishes (``residency_of``,
``memory``, ``stats``, ``store.search``, ``pending_corrections``) for the rest. The
sync tests use a real depot on a real socket and a real outbox; nothing here is
mocked. Every shard is closed and destroyed.
"""

from __future__ import annotations

import itertools
import time
from types import SimpleNamespace

import pytest

from edgemem.domain import (
    AuthorityClass,
    Claim,
    Residency,
    VerdictKind,
    utcnow,
)
from edgemem.memory import EdgeMemory
from edgemem.outbox import Outbox
from edgemem.residency import estimate_outbound_bytes
from edgemem.store import ShardStore
from edgemem.sync import Depot, DeviceLink
from edgemem.trust import TrustPolicy

SEAL = "trailer T-114 seal"
QUESTION = "seal state"
PROBE = "reefer probe"


def claim(**over) -> Claim:
    now = utcnow()
    base = {
        "subject": SEAL,
        "attribute": "state",
        "value": "broken",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": "TRK-7",
        "observed_at": now,
        "recorded_at": now,
        "salience": 0.8,
        "urgency": 0.8,
    }
    base.update(over)
    return Claim(**base)


# ==========================================================================
# 03: both retrieval paths, named and timed
# ==========================================================================


@pytest.fixture
def device(tmp_path):
    store = ShardStore(tmp_path / "mutable", device_id="TRK-7")
    store.open()
    mem = EdgeMemory(store)
    yield mem
    store.close()
    store.destroy()


@pytest.fixture
def store(tmp_path):
    s = ShardStore(tmp_path / "mutable", device_id="TRK-7")
    s.open()
    yield s
    s.close()
    s.destroy()


class _RecordingShard:
    """The real shard, with every request it was handed written down.

    Delegates by attribute so the store cannot tell it is being watched, and
    returns the engine's own answer untouched — the point is to observe the
    substrate boundary, not to stand in for it.
    """

    def __init__(self, shard, log: list[tuple[object, list]]) -> None:
        self._shard = shard
        self._log = log

    def __getattr__(self, name):
        return getattr(self._shard, name)

    def query(self, request):
        results = list(self._shard.query(request))
        self._log.append((request, results))
        return results


class _WatchedStore(ShardStore):
    """A store that keeps a record of every query that reached the shard."""

    def __init__(self, path, device_id="TRK-7") -> None:
        super().__init__(path, device_id=device_id)
        self.queries: list[tuple[object, list]] = []

    def _require(self):
        return _RecordingShard(super()._require(), self.queries)


def test_one_query_runs_a_dense_leg_and_a_keyword_leg_and_the_engine_fuses_them(
    tmp_path,
):
    """Checkbox: a query runs both retrieval paths.

    Observed at the substrate boundary rather than inferred from a label. One
    request carries two prefetches — the dense content vector and the sparse
    ``kw`` vector — and the library's own ``Fusion.Rrf`` combines them. The two
    further single-leg requests are the attribution pass and nothing else.
    """
    watched = _WatchedStore(tmp_path / "watched")
    watched.open()
    try:
        watched.upsert([claim()])
        watched.queries.clear()

        found = watched.traced_search(QUESTION, limit=5)

        assert found.paths == ("dense", "keyword")
        assert len(watched.queries) == 3, (
            f"expected one fused request and one read per leg, got "
            f"{len(watched.queries)}: {[repr(q) for q, _ in watched.queries]}"
        )

        request, fused_results = watched.queries[0]
        assert len(request.prefetches) == 2
        assert repr(request.query) == "Fusion.Rrf(k=2)", (
            "the two legs were not fused by the engine"
        )
        assert repr(request.prefetches[0].query).endswith("using=None)"), (
            f"the first leg is not the dense one: {request.prefetches[0].query!r}"
        )
        assert repr(request.prefetches[1].query).endswith('using="kw")'), (
            f"the second leg is not the keyword one: {request.prefetches[1].query!r}"
        )

        for leg_request, _ in watched.queries[1:]:
            assert leg_request.prefetches == [], "a leg request asked for a fusion"
            assert leg_request.with_payload is False, (
                "the attribution pass carried payloads it had no use for"
            )

        # The answer is the fused request's, row for row and score for score.
        assert [hit.claim.claim_id for hit in found.hits] == [
            str(rec.payload["claim_id"]) for rec in fused_results
        ]
        assert [hit.score for hit in found.hits] == [
            float(rec.score) for rec in fused_results
        ]
    finally:
        watched.close()
        watched.destroy()


def test_the_fused_scores_are_neither_leg_passing_through(store):
    """Checkbox: fusion is the engine's, and no score is combined in Python.

    Reciprocal rank fusion produces scores that are neither the dense leg's nor
    the keyword leg's. If a leg were passing through, its own score would appear
    on both sides of the comparison and this would fail.
    """
    store.upsert(
        [
            claim(value="broken"),
            claim(
                subject=PROBE,
                attribute="temperature",
                value="minus eighteen degrees",
                observer="probe-9",
            ),
        ]
    )
    text = "what state is the trailer T-114 seal in"

    fused, _ = store.search(text, limit=5)
    dense, _ = store.search(text, limit=5, use_dense=True, use_keyword=False)
    keyword, _ = store.search(text, limit=5, use_dense=False, use_keyword=True)

    def scores(rows):
        return {c.claim_id: score for c, score, _ in rows}

    fused_scores, dense_scores, keyword_scores = (
        scores(fused),
        scores(dense),
        scores(keyword),
    )
    shared = set(dense_scores) & set(keyword_scores)
    assert shared, "no claim was returned by both legs, so nothing was fused"
    for cid in shared:
        assert fused_scores[cid] not in (dense_scores[cid], keyword_scores[cid]), (
            f"claim {cid} carries one leg's own score: the fusion is a passthrough"
        )


def test_the_verdict_names_both_retrieval_paths(device):
    """Checkbox: the verdict names both paths.

    Names them as the query ran them, which is a fact about the search rather
    than about its hits: a question that matched nothing still ran both paths.
    """
    device.record(claim())
    answered = device.ask(QUESTION, subject=SEAL)
    assert answered.paths_used == ("dense", "keyword")

    # A question this device holds nothing about still names what it ran.
    empty = device.ask("what happened to the turbine gearbox", subject="turbine gearbox")
    assert empty.paths_used == ("dense", "keyword")


def test_the_verdict_says_which_claim_came_from_which_path(device):
    """Checkbox: which claims came from each path, not merely that both ran.

    Twenty low-salience readings share the query's common words, so the dense leg
    fills its list with them and the rare exact term is left for the keyword leg
    alone. Both legs are therefore measured on the same corpus, and the attribution
    the verdict reports is the difference between them.
    """
    rare = claim(value="customer Halvorsen notified", observer="handheld")
    device.record(rare)
    device.record_many(
        [
            claim(subject=f"unit-{n:02d}", attribute="reading", value="log")
            for n in range(20)
        ]
    )
    wording = "reading log handheld halvorsen"

    keyword_only = device.ask(wording, subject=SEAL)
    assert keyword_only.kind is VerdictKind.ANSWERED_LOCALLY
    assert keyword_only.paths_used == ("dense", "keyword"), "both legs ran"
    assert [c.claim_id for c in keyword_only.claims] == [rare.claim_id]
    assert keyword_only.claims[0].paths == ("keyword",), (
        "the rare term was found by the dense leg as well, so the attribution "
        "proves nothing"
    )

    # The same question, the same two paths, and a claim the two legs disagree
    # about — one they both returned, attributed to both rather than to one.
    both = device.ask(wording, subject="unit-02")
    assert both.paths_used == ("dense", "keyword")
    assert len(both.claims) == 1
    assert both.claims[0].paths == ("dense", "keyword")


def test_a_reworded_question_is_attributed_to_the_dense_leg_alone(device):
    """The other half of the same distinction.

    Both paths ran and are both named, and only one of them returned anything —
    which is the difference between naming the paths a query ran and naming where
    each claim actually came from.
    """
    device.record(
        claim(
            subject=PROBE,
            attribute="temperature",
            value="minus eighteen degrees",
            observer="probe-9",
        )
    )
    wording = "how cold did the chill box get"

    keyword, _ = device.store.search(
        wording, limit=5, use_dense=False, use_keyword=True
    )
    assert keyword == [], "the wording shared a term; the attribution proves nothing"

    verdict = device.ask(wording, subject=PROBE)

    assert verdict.kind is VerdictKind.ANSWERED_LOCALLY
    assert verdict.paths_used == ("dense", "keyword")
    assert len(verdict.claims) == 1
    assert verdict.claims[0].paths == ("dense",)
    assert set(verdict.claims[0].paths) <= set(verdict.paths_used)


def test_a_question_whose_wording_differs_from_the_stored_claim_returns_it(store):
    """Checkbox: a reworded question still finds the claim.

    The wording shares no token at all with the indexed text, so the keyword leg
    returns nothing — which is the point. What answers the question is the other
    leg.
    """
    cold = claim(
        subject=PROBE,
        attribute="temperature",
        value="minus eighteen degrees",
        observer="probe-9",
    )
    store.upsert([cold])
    wording = "how cold did the chill box get"

    keyword, _ = store.search(wording, limit=5, use_dense=False, use_keyword=True)
    assert keyword == [], "the wording shared a term; the test proves nothing"

    fused, _ = store.search(wording, limit=5)
    assert [c.claim_id for c, _, _ in fused] == [cold.claim_id]


def test_a_reworded_question_is_answered_locally_through_the_seam(device):
    device.record(
        claim(
            subject=PROBE,
            attribute="temperature",
            value="minus eighteen degrees",
            observer="probe-9",
        )
    )
    verdict = device.ask("how cold did the chill box get", subject=PROBE)
    assert verdict.kind is VerdictKind.ANSWERED_LOCALLY
    assert "minus eighteen degrees" in verdict.summary


def test_a_query_using_an_exact_stored_term_returns_that_claim(device):
    """Checkbox: an exact stored term finds the claim.

    Asserted through the seam and against the keyword leg alone, so the credit
    belongs to the leg that did the matching rather than to the fusion.
    """
    rare = device.record(claim(value="customer Halvorsen notified", observer="handheld"))

    verdict = device.ask("Halvorsen", subject=SEAL)

    assert verdict.kind is VerdictKind.ANSWERED_LOCALLY
    assert [c.claim_id for c in verdict.claims] == [rare.claim_id]
    assert "keyword" in verdict.claims[0].paths

    keyword, _ = device.store.search(
        "Halvorsen", limit=5, use_dense=False, use_keyword=True
    )
    assert [c.claim_id for c, _, _ in keyword] == [rare.claim_id]


def test_the_reported_latency_is_measured_inside_the_call_that_produced_it(device):
    """Checkbox: latency is measured, not estimated.

    A measurement cannot exceed the wall-clock interval that enclosed the call
    that produced it. A constant, or a figure modelled from a corpus size, has no
    such bound to respect.
    """
    device.record(claim())

    start = time.perf_counter()
    verdict = device.ask(QUESTION, subject=SEAL)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert 0.0 < verdict.latency_ms <= elapsed_ms


def test_the_reported_latency_is_read_from_the_clock(device, monkeypatch):
    """Checkbox: reported, not estimated — the number comes from a clock.

    The clock is replaced with one whose readings are a whole second apart, so a
    latency taken off it is a whole number of seconds in milliseconds. No
    hard-coded or modelled figure can be a multiple of a thousand that the
    engine did not measure.
    """
    device.record(claim())
    ticks = itertools.count(0.0, 1.0)
    monkeypatch.setattr(time, "perf_counter", lambda: next(ticks))

    verdict = device.ask(QUESTION, subject=SEAL)

    assert verdict.latency_ms >= 1000.0, (
        f"{verdict.latency_ms} ms is not a whole number of clock seconds"
    )
    assert verdict.latency_ms % 1000.0 == 0.0


# ==========================================================================
# 05: a claim that never leaves the device
# ==========================================================================


@pytest.fixture
def sealed_world(tmp_path):
    """One real depot on a real socket and one real device with a real outbox.

    The device's trust gate is open for RUMOUR, which is what puts the sealed
    claim out of reach of an answer while leaving it readable and still sealed by
    the sensitivity veto. Both are existing engine surfaces; nothing is stubbed.
    """
    depot_store = ShardStore(tmp_path / "depot-shard", device_id="DEPOT-1")
    depot_store.open()
    depot = Depot(
        EdgeMemory(depot_store), tmp_path / "ledger.jsonl", depot_id="DEPOT-1"
    )
    depot.serve()

    field_store = ShardStore(tmp_path / "field-shard", device_id="TRK-7")
    field_store.open()
    field = EdgeMemory(
        field_store,
        outbox=Outbox(tmp_path / "field-outbox.jsonl"),
        trust=TrustPolicy(held_classes={AuthorityClass.RUMOUR}),
    )
    link = DeviceLink(
        field, field.outbox, depot.url, tmp_path / "cursor.json"
    )

    yield SimpleNamespace(field=field, depot=depot, link=link)

    depot.stop()
    for s in (field_store, depot_store):
        s.close()
        s.destroy()


def sealed(**over) -> Claim:
    """A claim the sensitivity veto seals, at maximum urgency.

    The source class is RUMOUR, which this deployment's trust gate holds, so the
    claim is also one the device will not answer from. Both gates act on the same
    claim and neither is mocked.
    """
    now = utcnow()
    base = {
        "subject": SEAL,
        "attribute": "state",
        "value": "seal broken, customer Halvorsen notified",
        "author": "unattributed dock feed",
        "observer": "handheld",
        "device_id": "TRK-7",
        "observed_at": now,
        "recorded_at": now,
        "source_class": AuthorityClass.RUMOUR,
        "sensitivity": 0.95,
        "salience": 1.0,
        "urgency": 1.0,
    }
    base.update(over)
    return Claim(**base)


def test_a_sensitive_claim_is_held_local_despite_maximum_urgency(sealed_world):
    """Checkbox: held LOCAL despite high urgency, reason names the veto.

    Urgency at 1.0 and salience at 1.0 are both past their thresholds, and the
    claim is still sealed. The reason says so, and still reports the urgency that
    would otherwise have carried it, because a veto that hid what it overrode
    would be a veto nobody could challenge.
    """
    field = sealed_world.field
    held = field.record(sealed())

    reason = field.residency_of(held)

    assert reason.residency is Residency.LOCAL
    assert reason.vetoed_by == "sensitivity"
    assert "vetoed by sensitivity" in reason.render()
    assert "urgency 1.00 >= 0.50" in reason.render()
    assert "sensitivity 0.95 >= 0.80" in reason.render()


def test_the_sealed_claim_is_absent_from_the_depot_after_a_real_sync(sealed_world):
    """Checkbox: provably absent from the depot after a sync.

    A real listener, a real request, a real acknowledgement. The absence is then
    checked four ways at the depot: its memory, its memory's own report, its
    ledger, and what it can answer when asked about the subject. A mock would
    have none of these to disagree with.
    """
    field, depot = sealed_world.field, sealed_world.depot
    held = field.record(sealed())

    report = sealed_world.link.sync()

    assert report.path == "depot_delta"
    assert report.withheld == 1, "the policy did not refuse to transmit it"
    assert (report.pushed, report.accepted, report.duplicates) == (0, 0, 0)
    assert report.bytes_sent > 0, "no request crossed the socket at all"
    assert report.depot_bytes_received > 0, "the depot read nothing"

    # The depot's own record of what it holds.
    assert depot.memory.store.get(held.claim_id) is None
    assert held.claim_id not in {c.claim_id for c in depot.memory.memory()}
    assert depot.memory.stats()["claims"] == 0
    assert depot.status()["claims"] == 0
    # The depot's order of change never named it, so no device waits on it.
    assert depot.status()["numbered"] == 0
    assert depot.ledger.seq_of(held.claim_id) is None
    # And the depot cannot answer the question the claim would have answered.
    blind = depot.memory.ask(QUESTION, subject=SEAL)
    assert blind.kind is VerdictKind.UNRESOLVED_CLOUD_REQUIRED
    assert blind.claims == ()

    # The device still holds it, which is the whole point.
    assert field.store.get(held.claim_id) is not None

    # A second reconnect must not retry its way past the policy.
    again = sealed_world.link.sync()
    assert (again.pushed, again.withheld) == (0, 1)
    assert depot.memory.stats()["claims"] == 0
    assert held.claim_id not in {c.claim_id for c in depot.memory.memory()}


def test_a_question_the_sealed_claim_would_answer_says_the_cloud_is_required(
    sealed_world,
):
    """Checkbox: a question that claim would answer returns UNRESOLVED_CLOUD_REQUIRED.

    Note the honest scope of this. The claim is sealed by the sensitivity veto,
    and a claim sealed by the sensitivity veto is answerable — staying local is
    what makes it answerable, which is asserted elsewhere and is not the
    behaviour this ticket is about. So the claim is also held by the trust gate,
    which is the engine's own statement that it will not answer from it. A claim
    sealed by sensitivity alone deliberately answers locally; making it refuse
    would contradict a decision this repository has already made on purpose.
    """
    field = sealed_world.field
    field.record(sealed())

    verdict = field.ask(QUESTION, subject=SEAL)

    assert verdict.kind is VerdictKind.UNRESOLVED_CLOUD_REQUIRED
    assert verdict.claims == (), "a withheld claim answered the question anyway"


def test_the_unresolved_verdict_names_the_claim_that_would_resolve_it(sealed_world):
    """Checkbox: the verdict names the specific claim."""
    field = sealed_world.field
    held = field.record(sealed())

    verdict = field.ask(QUESTION, subject=SEAL)

    assert [n.claim_id for n in verdict.needed] == [held.claim_id]
    needed = verdict.needed[0]
    assert needed.subject == SEAL
    assert needed.attribute == "state"
    assert needed.device_id == "TRK-7"
    assert "RUMOUR" in needed.why_withheld
    assert "quarantined" in needed.why_withheld


def test_the_unresolved_verdict_states_the_content_and_the_byte_cost(sealed_world):
    """Checkbox: content and byte cost of what would need to be sent.

    The byte figure is the same one the residency policy measured for the
    decision that refused to send it, not a fresh estimate attached afterwards.
    """
    field = sealed_world.field
    held = field.record(sealed())

    verdict = field.ask(QUESTION, subject=SEAL)

    needed = verdict.needed[0]
    assert needed.value == "seal broken, customer Halvorsen notified"
    assert needed.bytes_if_sent > 0
    assert needed.bytes_if_sent == estimate_outbound_bytes(held)
    assert verdict.bytes_withheld == needed.bytes_if_sent
    assert f"outbound_bytes {needed.bytes_if_sent}" in (
        " ".join(field.residency_of(held).signals)
    )


def test_the_claim_is_absent_from_the_depot_and_the_verdict_still_names_it(
    sealed_world,
):
    """Checkbox: one test asserts the absence and the naming together."""
    field, depot = sealed_world.field, sealed_world.depot
    held = field.record(sealed())

    report = sealed_world.link.sync()
    assert (report.withheld, report.accepted) == (1, 0)
    assert depot.memory.stats()["claims"] == 0
    assert depot.ledger.seq_of(held.claim_id) is None

    verdict = field.ask(QUESTION, subject=SEAL)

    assert verdict.kind is VerdictKind.UNRESOLVED_CLOUD_REQUIRED
    assert held.claim_id in {n.claim_id for n in verdict.needed}
    assert verdict.bytes_withheld > 0


# ==========================================================================
# 09: the same question answers differently after sync
# ==========================================================================


@pytest.fixture
def fleet(tmp_path):
    """Two real devices and a real depot, each with a real outbox and cursor."""
    depot_store = ShardStore(tmp_path / "depot-shard", device_id="DEPOT-1")
    depot_store.open()
    depot = Depot(
        EdgeMemory(depot_store), tmp_path / "ledger.jsonl", depot_id="DEPOT-1"
    )
    depot.serve()

    def make(device_id: str) -> EdgeMemory:
        s = ShardStore(tmp_path / f"shard-{device_id}", device_id=device_id)
        s.open()
        made.append(s)
        return EdgeMemory(s, outbox=Outbox(tmp_path / f"outbox-{device_id}.jsonl"))

    made: list[ShardStore] = []
    dock = make("TRK-7")
    field = make("TRK-9")
    dock_link = DeviceLink(dock, dock.outbox, depot.url, tmp_path / "dock.json")
    field_link = DeviceLink(field, field.outbox, depot.url, tmp_path / "field.json")

    yield SimpleNamespace(
        dock=dock, field=field, depot=depot, dock_link=dock_link, field_link=field_link
    )

    depot.stop()
    for s in made + [depot_store]:
        s.close()
        s.destroy()


def dock_reading(**over) -> Claim:
    """What the dock scanner saw, before the field device had ever synced."""
    now = utcnow()
    base = {
        "subject": SEAL,
        "attribute": "state",
        "value": "broken",
        "author": "dock operator 3",
        "observer": "dock-scanner",
        "device_id": "TRK-7",
        "observed_at": now,
        "recorded_at": now,
        "source_class": AuthorityClass.INSTRUMENT,
        "salience": 0.9,
        "urgency": 0.9,
    }
    base.update(over)
    return Claim(**base)


def field_note(**over) -> Claim:
    """What the field device saw. An empty causal context, like the dock's.

    Both were perceived from the same point in history — neither had seen the
    other when either was written — so the two are not concurrent and the arrival
    reads as a revision of what the device believed rather than as a
    disagreement it must escalate. A CONFLICTED verdict would have been a true
    statement about concurrency and the wrong statement about this ticket.
    """
    now = utcnow()
    base = {
        "subject": SEAL,
        "attribute": "state",
        "value": "intact",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": "TRK-9",
        "observed_at": now,
        "recorded_at": now,
        "source_class": AuthorityClass.UNATTESTED_HUMAN,
        "salience": 0.9,
        "urgency": 0.9,
    }
    base.update(over)
    return Claim(**base)


def offline_then_reconnect(place: SimpleNamespace) -> tuple[Claim, Claim, object]:
    """The ticket's shape: ask offline, then sync, then ask the same thing.

    Returns the claim that will arrive, the claim the device already had, and the
    verdict it gave before any of it happened.
    """
    arriving = place.dock.record(dock_reading())
    place.dock_link.sync()

    held = place.field.record(field_note())
    before = place.field.ask(QUESTION, subject=SEAL)
    return arriving, held, before


def test_a_claim_arriving_from_the_depot_changes_what_the_device_believes(fleet):
    """Checkbox: an arriving claim changes what the device believes."""
    arriving, held, before = offline_then_reconnect(fleet)
    assert before.kind is VerdictKind.ANSWERED_LOCALLY
    assert [c.claim_id for c in before.claims] == [held.claim_id]

    report = fleet.field_link.sync()
    assert report.pulled == 1, "the device did not learn anything"

    after = fleet.field.ask(QUESTION, subject=SEAL)
    assert {c.claim_id for c in after.claims} == {held.claim_id, arriving.claim_id}
    assert after.summary != before.summary


def test_the_same_question_answers_differently_before_and_after_a_sync(fleet):
    """Checkbox: the same question yields different verdicts."""
    _, _, before = offline_then_reconnect(fleet)
    assert before.kind is VerdictKind.ANSWERED_LOCALLY

    fleet.field_link.sync()
    after = fleet.field.ask(QUESTION, subject=SEAL)

    assert after.kind is VerdictKind.CORRECTED
    assert after.kind is not before.kind
    assert after.subject == before.subject
    assert after.question == before.question


def test_the_corrected_verdict_carries_the_earlier_answer_beside_the_new_one(fleet):
    """Checkbox: CORRECTED, with the earlier answer alongside the new one."""
    _, _, before = offline_then_reconnect(fleet)

    fleet.field_link.sync()
    after = fleet.field.ask(QUESTION, subject=SEAL)

    assert after.previous_summary == before.summary
    assert after.previous_summary != after.summary
    assert after.claims, "the new answer carries no claims at all"
    payload = after.to_payload()
    assert payload["previous_summary"] == before.summary
    assert payload["kind"] == "CORRECTED"


def test_the_corrected_verdict_names_the_claim_that_caused_the_change(fleet):
    """Checkbox: the verdict names the claim responsible.

    Not the claim that happened to be ranked first, and not the whole set: the
    one claim the previous answer did not stand behind.
    """
    arriving, held, _ = offline_then_reconnect(fleet)

    fleet.field_link.sync()
    after = fleet.field.ask(QUESTION, subject=SEAL)

    assert after.changed_by == arriving.claim_id
    assert after.changed_by != held.claim_id


def test_the_device_says_a_correction_is_pending_without_being_asked_again(fleet):
    """Checkbox: visible to an operator without needing to ask twice.

    The device has to be able to say that what it told somebody is no longer
    what it holds. Asking again would find out, but only if the operator guessed
    to ask again — which is precisely the burden this removes.

    Nothing is asked here. No question is issued and no verdict is produced; the
    notice names the stale question, the answer the device stands behind, and the
    claims that arrived behind it. And it clears itself the moment the question is
    asked, because those claims are then behind the recorded answer.
    """
    arriving, _, before = offline_then_reconnect(fleet)
    assert fleet.field.pending_corrections() == ()

    fleet.field_link.sync()

    pending = fleet.field.pending_corrections()
    assert len(pending) == 1
    entry = pending[0]
    assert entry.subject == SEAL
    assert entry.question == QUESTION
    assert entry.previous_summary == before.summary
    assert entry.claim_ids == (arriving.claim_id,)
    assert entry.render(), "the notice rendered as nothing"
    assert entry.to_payload()["claim_ids"] == [arriving.claim_id]

    # No second question was asked to produce any of that.
    assert fleet.field.stats()["answered_questions"] == 1
    assert fleet.field.stats()["pending_corrections"] == 1
    assert fleet.field.log.previous(SEAL, QUESTION) == before.summary

    # Asking clears it, with nothing to acknowledge.
    fleet.field.ask(QUESTION, subject=SEAL)
    assert fleet.field.pending_corrections() == ()
    assert fleet.field.stats()["pending_corrections"] == 0


def test_a_pending_correction_is_not_raised_for_another_aspect_of_the_subject(
    fleet,
):
    """A claim about a different aspect does not make an answer stale.

    The narrowing is the same one `ask` applies, so a device that was asked about
    the trailer's state is not told its state is out of date because somebody
    else recorded who is holding the trailer.
    """
    offline_then_reconnect(fleet)
    fleet.field.record(
        claim(attribute="custody", value="held by dock 4", device_id="TRK-9")
    )
    custody = fleet.field.ask("custody", subject=SEAL)
    assert custody.kind is VerdictKind.ANSWERED_LOCALLY
    assert fleet.field.pending_corrections() == ()

    fleet.field_link.sync()

    subjects = {p.question for p in fleet.field.pending_corrections()}
    assert QUESTION in subjects
    assert "custody" not in subjects, (
        "a claim about the trailer's custody was reported as a change to its state"
    )


def test_the_same_question_before_and_after_a_sync_changed_and_names_the_cause(
    fleet,
):
    """Checkbox: the ticket's own acceptance test, end to end over a real link."""
    arriving, held, before = offline_then_reconnect(fleet)
    assert before.kind is VerdictKind.ANSWERED_LOCALLY

    report = fleet.field_link.sync()
    assert (report.pulled, report.accepted) == (1, 1)

    after = fleet.field.ask(QUESTION, subject=SEAL)

    assert after.kind is VerdictKind.CORRECTED
    assert after.previous_summary == before.summary
    assert after.changed_by == arriving.claim_id
    assert {c.claim_id for c in after.claims} == {held.claim_id, arriving.claim_id}