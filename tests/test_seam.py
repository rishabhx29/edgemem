"""The question seam, end to end.

Every test here drives the device the way a caller does: ask a question,
receive a verdict. Nothing reaches inside. If a test can only pass by knowing
the internals, it belongs in a different file.
"""

from __future__ import annotations

import pytest

from edgemem.domain import AuthorityClass, Claim, Trust, utcnow
from edgemem.memory import EdgeMemory, Ladder
from edgemem.store import ShardStore


@pytest.fixture
def device(tmp_path):
    store = ShardStore(tmp_path / "mutable", device_id="TRK-7")
    store.open()
    mem = EdgeMemory(store)
    yield mem
    store.close()
    store.destroy()


def claim(**over) -> Claim:
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
        "salience": 0.8,
        "urgency": 0.8,
    }
    base.update(over)
    return Claim(**base)


# --------------------------------------------------------------------------
# ANSWERED_LOCALLY
# --------------------------------------------------------------------------


def test_a_question_the_device_holds_is_answered_locally(device):
    device.record(claim(value="broken"))
    verdict = device.ask("what state is the trailer T-114 seal in", subject="trailer T-114 seal")

    assert verdict.kind.value == "ANSWERED_LOCALLY"
    assert verdict.claims
    assert "broken" in verdict.summary
    assert verdict.latency_ms > 0.0


def test_an_answered_verdict_cites_what_it_used(device):
    original = claim(value="broken")
    device.record(original)
    verdict = device.ask("seal state", subject="trailer T-114 seal")

    cited = verdict.claims[0]
    assert cited.claim_id == original.claim_id
    assert cited.author == "operator-7"
    assert cited.observer == "handheld"
    assert cited.reason, "a citation must carry the reason it was usable"


def test_the_verdict_names_the_retrieval_paths_it_used(device):
    device.record(claim())
    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert verdict.paths_used, "the verdict must name its retrieval paths"


def test_the_device_reports_its_index_honestly(device):
    device.record(claim())
    assert device.index_state()
    assert "claims" in device.stats()


# --------------------------------------------------------------------------
# UNRESOLVED_CLOUD_REQUIRED
# --------------------------------------------------------------------------


def test_a_question_the_device_cannot_answer_says_so(device):
    verdict = device.ask("what happened to the turbine gearbox", subject="turbine gearbox")
    assert verdict.kind.value == "UNRESOLVED_CLOUD_REQUIRED"
    assert "cannot" in verdict.summary.lower() or "no" in verdict.summary.lower()


def test_a_sensitive_claim_answers_locally_because_it_never_left(device):
    """LOCAL is the normal case, not a withholding.

    A claim that stays on the device is answerable from memory precisely
    because it never left, and its reason must be visible in the citation.
    """
    device.record(claim(value="seal broken, customer Halvorsen notified", sensitivity=0.95))

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert verdict.kind.value == "ANSWERED_LOCALLY"
    cited = verdict.claims[0]
    assert cited.residency == "LOCAL"
    assert "sensitivity" in cited.reason
    assert verdict.bytes_withheld == 0


def test_a_withheld_claim_is_named_and_priced(device):
    """CLOUD_ONLY and quarantined claims cannot answer, and are named as missing."""
    device.record(claim(value="temperature within band", attribute="temperature"))
    held = claim(value="seal broken, customer Halvorsen notified")
    device.record(held)
    # The device's own record shows this one is already at the depot.
    device.mark_present_at_depot([held.claim_id])

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert verdict.kind.value == "UNRESOLVED_CLOUD_REQUIRED"
    assert verdict.needed, "a withheld claim must be named"
    needed = verdict.needed[0]
    assert needed.claim_id == held.claim_id
    assert needed.bytes_if_sent > 0
    assert needed.why_withheld
    assert verdict.bytes_withheld == needed.bytes_if_sent


def test_a_quarantined_claim_is_named_as_missing(device):
    device.record(claim(value="seal broken"))
    device.record(
        claim(value="rumoured theft from trailer").with_trust(
            Trust.QUARANTINED, "source class RUMOUR"
        )
    )

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert verdict.needed, "a quarantined claim must be named as unavailable"
    assert any("RUMOUR" in n.why_withheld for n in verdict.needed)


# --------------------------------------------------------------------------
# CONFLICTED
# --------------------------------------------------------------------------


def test_two_concurrent_claims_come_back_as_a_conflict(device):
    """Both written from the same prior state, neither having seen the other."""
    left = claim(
        value="broken",
        source_class=AuthorityClass.UNATTESTED_HUMAN,
    )
    left.causal.observe("TRK-7")
    right = claim(
        value="intact",
        observer="dock-scanner",
        source_class=AuthorityClass.INSTRUMENT,
        device_id="DEPOT-1",
    )
    right.causal.observe("DEPOT-1")

    device.record(left)
    device.record(right)

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert verdict.kind.value == "CONFLICTED"
    assert len(verdict.conflicts) == 1
    a, b = verdict.conflicts[0]
    assert {a.claim.value, b.claim.value} == {"broken", "intact"}


def test_a_conflict_retains_both_sides_with_attribution(device):
    left = claim(value="broken")
    left.causal.observe("TRK-7")
    right = claim(value="intact", observer="dock-scanner", device_id="DEPOT-1")
    right.causal.observe("DEPOT-1")
    device.record_many([left, right])

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    a, b = verdict.conflicts[0]
    for side in (a, b):
        assert side.claim.claim_id
        assert side.claim.author
        assert side.claim.observer
        assert side.claim.device_id
        assert side.claim.observed_at


def test_a_conflict_does_not_resolve_itself(device):
    left = claim(value="broken")
    left.causal.observe("TRK-7")
    right = claim(value="intact", device_id="DEPOT-1")
    right.causal.observe("DEPOT-1")
    device.record_many([left, right])

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert "not choosing" in verdict.summary.lower()
    assert verdict.authority is not None
    assert verdict.authority.note


def test_the_authority_ladder_is_presented_not_applied(device):
    left = claim(value="broken", source_class=AuthorityClass.RUMOUR)
    left.causal.observe("TRK-7")
    right = claim(
        value="intact",
        observer="dock-scanner",
        source_class=AuthorityClass.INSTRUMENT,
        device_id="DEPOT-1",
    )
    right.causal.observe("DEPOT-1")
    device.record_many([left, right])

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert verdict.authority.entitling_class == AuthorityClass.INSTRUMENT.value
    assert verdict.authority.ladder_version
    # Both sides survive regardless of which outranks which.
    a, b = verdict.conflicts[0]
    assert a.claim.value and b.claim.value


def test_a_recent_claim_is_not_a_resolution(device):
    """A later claim that saw the earlier one is a revision, not a conflict."""
    original = claim(value="intact")
    device.record(original)
    revised = claim(value="broken", supersedes=original.claim_id)
    device.record(revised)

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert verdict.kind.value != "CONFLICTED"


def test_supports_are_counted_per_side(device):
    left = claim(value="broken")
    left.causal.observe("TRK-7")
    right = claim(value="intact", device_id="DEPOT-1")
    right.causal.observe("DEPOT-1")
    device.record_many([left, right])

    verdict = device.ask("seal state", subject="trailer T-114 seal")
    a, b = verdict.conflicts[0]
    assert a.supporters >= 1
    assert b.supporters >= 1


# --------------------------------------------------------------------------
# CORRECTED
# --------------------------------------------------------------------------


def test_the_same_question_can_answer_differently_after_learning(device):
    device.record(claim(value="broken"))
    before = device.ask("seal state", subject="trailer T-114 seal")
    assert before.kind.value == "ANSWERED_LOCALLY"
    assert "broken" in before.summary

    device.record(claim(value="intact", attribute="state", supersedes=None))
    after = device.ask("seal state", subject="trailer T-114 seal")

    assert after.kind.value in {"CORRECTED", "CONFLICTED"}
    if after.kind.value == "CORRECTED":
        assert after.previous_summary == before.summary
        assert after.changed_by


def test_asking_the_same_question_twice_is_stable(device):
    device.record(claim(value="broken"))
    first = device.ask("seal state", subject="trailer T-114 seal")
    second = device.ask("seal state", subject="trailer T-114 seal")
    assert first.summary == second.summary
    assert first.kind == second.kind


# --------------------------------------------------------------------------
# quarantine
# --------------------------------------------------------------------------


def test_a_quarantined_claim_does_not_answer(device):
    device.record(claim().with_trust(Trust.QUARANTINED, "source class RUMOUR"))
    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert verdict.kind.value == "UNRESOLVED_CLOUD_REQUIRED"
    assert not [c for c in verdict.claims if c.trust == Trust.QUARANTINED.value]


def test_quarantine_is_inspectable(device):
    held = claim(value="rumoured break").with_trust(
        Trust.QUARANTINED, "source class RUMOUR"
    )
    device.record(held)
    found = [c for c in device.memory() if c.claim_id == held.claim_id]
    assert found and found[0].is_quarantined()
    assert found[0].quarantine_reason == "source class RUMOUR"


# --------------------------------------------------------------------------
# the verdict shape does not vary
# --------------------------------------------------------------------------


def test_every_verdict_has_the_same_shape(device):
    device.record(claim(value="broken"))
    answered = device.ask("seal state", subject="trailer T-114 seal")
    unresolved = device.ask("unrelated subject", subject="something else")

    for v in (answered, unresolved):
        payload = v.to_payload()
        for key in (
            "kind",
            "subject",
            "question",
            "summary",
            "claims",
            "conflicts",
            "authority",
            "needed",
            "previous_summary",
            "latency_ms",
            "paths_used",
            "bytes_withheld",
        ):
            assert key in payload, f"{v.kind} verdict is missing {key}"


def test_a_verdict_serialises_to_plain_data(device):
    import json

    device.record(claim())
    verdict = device.ask("seal state", subject="trailer T-114 seal")
    assert json.loads(json.dumps(verdict.to_payload()))


# --------------------------------------------------------------------------
# the ladder is data
# --------------------------------------------------------------------------


def test_the_ladder_is_replaceable_without_changing_the_engine(tmp_path):
    store = ShardStore(tmp_path / "mutable", device_id="TRK-7")
    store.open()
    try:
        strict = Ladder(
            version="v2",
            entries=((AuthorityClass.RUMOUR, 100), (AuthorityClass.INSTRUMENT, 10)),
        )
        mem = EdgeMemory(store, ladder=strict)

        left = claim(value="broken", source_class=AuthorityClass.RUMOUR)
        left.causal.observe("TRK-7")
        right = claim(value="intact", source_class=AuthorityClass.INSTRUMENT, device_id="D1")
        right.causal.observe("D1")
        mem.record_many([left, right])

        verdict = mem.ask("seal state", subject="trailer T-114 seal")
        assert verdict.authority.ladder_version == "v2"
        assert verdict.authority.entitling_class == AuthorityClass.RUMOUR.value
    finally:
        store.close()
        store.destroy()
