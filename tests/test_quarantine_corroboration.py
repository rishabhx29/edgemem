"""Quarantine and corroboration, end to end.

A claim from a source the device does not trust is held apart, not refused: it
stays on the shard, it stays readable, and it cannot answer anything. Releasing
it is something a person does, and the record says who. Everything here is
reached the way a caller reaches it -- record something, ask a question, read a
verdict, look at the memory -- and nothing reaches inside the engine.

The second half of the file is about a number that lies. A rumour written down
three times is three claims and one source, and a verdict that reported only "two
supporting claims" would have turned one voice into a chorus. So every citation
and every conflict side says which source classes are behind the claim, and a
claim that nobody but its own source class agrees with says so in the verdict's
own summary rather than waiting to be inferred from a figure.
"""

from __future__ import annotations

import pytest

from edgemem.domain import AuthorityClass, CausalContext, Claim, Trust, utcnow
from edgemem.memory import EdgeMemory
from edgemem.store import ShardStore
from edgemem.trust import DEFAULT_TRUST_POLICY, TrustPolicy
from fixtures import trucks

SUBJECT = "trailer T-114 seal"
QUESTION = "seal state"
HAND = "TRK-7"
DOCK = "DOCK-2"

GATE = TrustPolicy(
    policy_version="gate-1",
    held_classes=frozenset({AuthorityClass.RUMOUR}),
)
"""A device whose operator has said it will not believe a rumour.

Not the engine's default and not a pack's to set: this is the deployment choosing
what it trusts, which is why it is supplied to the device rather than bound to a
vertical.
"""


@pytest.fixture
def open_device(tmp_path):
    """Builds devices on one shard directory, so a restart can be asked for.

    Yields the factory rather than a device, because some of what follows needs a
    second device over the same shard. Every shard handed out is closed and
    destroyed here, so a test that restarts a device cannot leak the shard the
    restart left behind.
    """
    opened: list[ShardStore] = []

    def build(**kwargs) -> EdgeMemory:
        store = ShardStore(tmp_path / "mutable", device_id=HAND)
        store.open()
        opened.append(store)
        return EdgeMemory(store, **kwargs)

    try:
        yield build
    finally:
        for store in opened:
            store.close()
            store.destroy()


@pytest.fixture
def device(open_device):
    """A device whose trust gate holds rumours."""
    return open_device(trust=GATE)


def claim(**over) -> Claim:
    now = utcnow()
    base = {
        "subject": SUBJECT,
        "attribute": "state",
        "value": "broken",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": HAND,
        "observed_at": now,
        "recorded_at": now,
        "salience": 0.8,
        "urgency": 0.8,
    }
    base.update(over)
    return Claim(**base)


def rumour(**over) -> Claim:
    return claim(
        **{"source_class": AuthorityClass.RUMOUR, "observer": "dockboard", **over}
    )


def offline(claim: Claim, device_id: str) -> Claim:
    """``claim`` stamped from a shared prior advanced by one device's write.

    Two claims are concurrent exactly when neither causal context dominates the
    other, which is what makes them a disagreement rather than a revision. Left at
    the default, two claims are not concurrent at all and the conflict under test
    below would never be reported.
    """
    causal = CausalContext({HAND: 1, DOCK: 1}).copy_for_write(device_id)
    causal.observe(device_id)
    return type(claim)(**{**claim.__dict__, "causal": causal})


def scanning(**over) -> Claim:
    return offline(
        claim(
            **{
                "value": "intact",
                "observer": "dock-scanner",
                "device_id": DOCK,
                "source_class": AuthorityClass.INSTRUMENT,
                **over,
            }
        ),
        DOCK,
    )


def ask(device: EdgeMemory):
    return device.ask(QUESTION, subject=SUBJECT)


def classes_of(corroboration) -> dict[str, int]:
    return dict(corroboration.classes)


def sides_of(verdict) -> dict[str, object]:
    return {side.claim.value: side for pair in verdict.conflicts for side in pair}


# --------------------------------------------------------------------------
# held apart, not refused
# --------------------------------------------------------------------------


def test_a_low_trust_source_is_quarantined_on_arrival(device):
    """The gate runs before the claim joins anything that answers.

    ``record`` hands back the claim the device actually kept, so a caller learns
    what happened to it without having to go looking.
    """
    kept = device.record(rumour())

    assert kept.is_quarantined()
    verdict = ask(device)
    assert verdict.kind.value == "UNRESOLVED_CLOUD_REQUIRED"
    assert verdict.claims == ()
    assert [n.claim_id for n in verdict.needed] == [kept.claim_id]
    assert "quarantined:" in verdict.needed[0].why_withheld


def test_a_held_claim_is_kept_and_inspectable_with_the_reason(device):
    """Quarantine is a judgement, not a deletion, and the reason is on the claim."""
    device.record(rumour(value="pallets missing from bay four"))

    held = [c for c in device.memory() if c.is_quarantined()]
    assert len(held) == 1
    assert held[0].value == "pallets missing from bay four"
    assert held[0].quarantine_reason, "a hold with no stated reason is not auditable"
    assert AuthorityClass.RUMOUR.value in held[0].quarantine_reason
    assert "gate-1" in held[0].quarantine_reason, (
        "the reason must name the policy version, or a later reader cannot "
        "reproduce the decision that held the claim"
    )


def test_a_held_claim_does_not_answer_beside_a_trusted_one(device):
    trusted = device.record(
        claim(value="broken", source_class=AuthorityClass.INSTRUMENT)
    )
    device.record(rumour(value="broken"))

    verdict = ask(device)
    assert verdict.kind.value == "ANSWERED_LOCALLY"
    assert [c.claim_id for c in verdict.claims] == [trusted.claim_id]
    assert all(c.trust != Trust.QUARANTINED.value for c in verdict.claims)


# --------------------------------------------------------------------------
# releasing is an action
# --------------------------------------------------------------------------


def test_releasing_a_held_claim_makes_it_answer(device):
    """Releasing a held claim changes the device's answer, so it is CORRECTED.

    Not ANSWERED_LOCALLY: the device could not answer before, and becoming able
    to answer is exactly the transition the CORRECTED verdict exists to report.
    A supervisor vouched for the claim, and that is now visible to the operator.
    """
    held = device.record(rumour())
    assert ask(device).kind.value == "UNRESOLVED_CLOUD_REQUIRED"

    released = device.release([held.claim_id], reason="supervisor vouched for it")

    assert [c.claim_id for c in released] == [held.claim_id]
    assert ask(device).kind.value == "CORRECTED"
    # Asked again with nothing new to learn, it settles.
    assert ask(device).kind.value == "ANSWERED_LOCALLY"
    on_record = device.memory()[0]
    assert on_record.was_released()
    assert on_record.release_note == "supervisor vouched for it"
    assert on_record.quarantine_reason, (
        "the reason it was held stays on the claim: a release adds to the record "
        "rather than overwriting it"
    )


def test_nothing_but_the_release_ends_a_quarantine(open_device):
    """Not time, not a rebuild, not a reconnect.

    The device is closed and a new one is opened on the same directory: everything
    in memory is gone and the claim comes back off the shard. It is still held,
    because nothing in the engine reconsiders a quarantine and nothing in it reads
    a clock to decide one.
    """
    first = open_device(trust=GATE)
    held = first.record(rumour())
    first.store.close()

    restarted = open_device(trust=GATE)
    assert ask(restarted).kind.value == "UNRESOLVED_CLOUD_REQUIRED"
    assert ask(restarted).kind.value == "UNRESOLVED_CLOUD_REQUIRED", (
        "asking again is not time passing, and it must release nothing"
    )
    assert [c.claim_id for c in restarted.memory() if c.is_quarantined()] == [
        held.claim_id
    ]
    assert not restarted.memory()[0].was_released()


def test_a_release_must_say_why(device):
    held = device.record(rumour())
    with pytest.raises(ValueError):
        device.release([held.claim_id], reason="   ")
    assert device.memory()[0].is_quarantined()


def test_a_release_reports_what_it_released_and_refuses_what_it_cannot(device):
    trusted = device.record(claim(value="intact"))
    held = device.record(rumour(value="broken"))

    assert device.release([trusted.claim_id], reason="already trusted") == [], (
        "a claim that was never held cannot be released"
    )
    assert device.release([], reason="nothing named") == []

    with pytest.raises(KeyError):
        device.release(["no-such-claim"], reason="a name the device does not hold")

    released = device.release([held.claim_id], reason="named, not invented")
    assert [c.claim_id for c in released] == [held.claim_id]


# --------------------------------------------------------------------------
# corroboration, per source class
# --------------------------------------------------------------------------


def test_corroboration_is_reported_per_source_class(open_device):
    """The count is broken out by who made the agreeing claim."""
    device = open_device()
    device.record_many(
        [
            claim(value="broken", source_class=AuthorityClass.INSTRUMENT),
            claim(value="broken", source_class=AuthorityClass.ATTESTED_HUMAN),
            claim(value="broken", source_class=AuthorityClass.THIRD_PARTY_FEED),
        ]
    )

    cited = ask(device).claims[0].corroboration
    assert classes_of(cited) == {
        AuthorityClass.INSTRUMENT.value: 1,
        AuthorityClass.ATTESTED_HUMAN.value: 1,
        AuthorityClass.THIRD_PARTY_FEED.value: 1,
    }
    assert cited.independent_classes == (
        AuthorityClass.INSTRUMENT.value,
        AuthorityClass.ATTESTED_HUMAN.value,
    )
    assert cited.echoes == 0
    assert cited.self_corroborated is False
    assert cited.voices == 3, "three source classes agreeing is three voices"


def test_one_source_class_in_every_order_is_one_voice(device):
    """Four rumours an operator let through are still one voice, and it says so.

    This is the anti-pattern the count exists to catch: the claims are released
    and answerable, a bare claim count reads as four, and every one of them came
    from the same source class.
    """
    held = [
        device.record(rumour(observer=f"board-{i}")).claim_id for i in range(4)
    ]
    device.release(held, reason="released one at a time, not as a chorus")

    verdict = ask(device)
    cited = verdict.claims[0]
    assert classes_of(cited.corroboration) == {AuthorityClass.RUMOUR.value: 4}
    assert cited.corroboration.echoes == 3
    assert cited.corroboration.voices == 1
    assert cited.corroboration.self_corroborated is True
    # The raw claim count is still reported, so the analysis hides nothing.
    assert cited.corroborations == 4


def test_self_corroboration_is_said_in_the_summary_not_only_in_a_field(device):
    """Visible before it is believed, rather than recoverable by arithmetic."""
    held = [
        device.record(rumour(observer=f"board-{i}")).claim_id for i in range(2)
    ]
    device.release(held, reason="released on the operator's word")

    summary = ask(device).summary
    assert AuthorityClass.RUMOUR.value in summary
    assert "no source class other than" in summary
    assert "1 further RUMOUR claim" in summary


def test_one_other_source_class_is_enough_to_stop_the_flag(open_device):
    device = open_device()
    device.record_many(
        [rumour(), claim(value="broken", source_class=AuthorityClass.INSTRUMENT)]
    )

    verdict = ask(device)
    for cited in verdict.claims:
        assert cited.corroboration.self_corroborated is False
        assert cited.corroboration.voices == 2
    assert "no source class other than" not in verdict.summary


def test_a_held_claim_does_not_become_a_supporter(device):
    """Writing the rumour down again is not a way around the gate."""
    device.record(claim(value="broken", source_class=AuthorityClass.INSTRUMENT))
    for i in range(3):
        device.record(rumour(observer=f"board-{i}"))

    cited = ask(device).claims[0].corroboration
    assert classes_of(cited) == {AuthorityClass.INSTRUMENT.value: 1}
    assert AuthorityClass.RUMOUR.value not in cited.independent_classes
    assert cited.echoes == 0
    assert cited.voices == 1


def test_a_held_claim_is_excluded_from_both_ends_of_a_conflict(device):
    """Three trusted notes say ``broken`` and a held rumour says the same.

    The notes' side is the one that would look like consensus if a claim the
    device refused to believe were allowed to help it.
    """
    for i in range(3):
        device.record(offline(claim(observer=f"handheld-{i}"), HAND))
    device.record(rumour(value="broken"))
    device.record(scanning())

    verdict = ask(device)
    assert verdict.kind.value == "CONFLICTED"
    notes, scanner = sides_of(verdict)["broken"], sides_of(verdict)["intact"]

    assert notes.supporters == 3, "the three trusted notes, and no more"
    assert classes_of(notes.corroboration) == {
        AuthorityClass.UNATTESTED_HUMAN.value: 3
    }
    assert notes.corroboration.voices == 1, (
        "three notes from one class are one voice"
    )
    assert scanner.supporters == 1


def test_a_conflict_side_says_when_only_its_own_source_agrees(open_device):
    device = open_device()
    device.record_many(
        [
            offline(rumour(), HAND),
            offline(rumour(observer="board-2"), HAND),
            scanning(),
        ]
    )

    verdict = ask(device)
    sides = sides_of(verdict)
    assert sides["broken"].corroboration.self_corroborated is True
    assert sides["broken"].corroboration.echoes == 1
    assert sides["intact"].corroboration.self_corroborated is False
    assert "corroborated only by their own source class" in verdict.summary
    assert "not choosing" in verdict.summary, "an echo is not a resolution"


def test_the_raw_claim_count_is_reported_alongside_the_analysis(device):
    """Two figures, and the caller is handed both rather than one of them."""
    device.record_many(
        [
            claim(value="broken", source_class=AuthorityClass.INSTRUMENT),
            claim(value="broken", source_class=AuthorityClass.ATTESTED_HUMAN),
        ]
    )

    cited = ask(device).claims[0]
    assert cited.corroborations == 2
    assert cited.corroboration.agreeing_claims == 2
    assert cited.corroboration.voices == 2
    assert cited.corroboration.render().startswith(
        f"{AuthorityClass.INSTRUMENT.value} 1"
    )


# --------------------------------------------------------------------------
# the gate is the engine's, not the vertical's
# --------------------------------------------------------------------------


def test_a_bound_vertical_cannot_open_or_close_the_trust_gate(open_device):
    """A pack supplies labels, ladder, subject model and citation. That is all.

    A device bound to a vertical and given no trust policy of its own holds a
    rumour no more than an unbound one does. A vertical must not be a way for one
    deployment's trust decisions to travel to every other device that binds it.
    """
    device = open_device(pack=trucks.PACK)
    device.record(rumour())

    assert device.stats()["trust_policy_version"] == DEFAULT_TRUST_POLICY.policy_version
    assert device.stats()["trust_held_classes"] == []
    assert device.stats()["trust_entitlement_floor"] is None
    assert not device.memory()[0].is_quarantined()


def test_the_gate_is_reported_where_an_operator_can_read_it(device):
    device.record(rumour())

    stats = device.stats()
    assert stats["quarantined"] == 1
    assert stats["trust_policy_version"] == "gate-1"
    assert stats["trust_held_classes"] == [AuthorityClass.RUMOUR.value]
    assert stats["trust_entitlement_floor"] is None


def test_a_gate_held_claim_survives_as_held_and_a_released_one_does_not(device):
    held = device.record(rumour(value="broken"))
    released = device.record(rumour(value="intact", observer="board-2"))
    device.release([released.claim_id], reason="board operator named themselves")

    stats = device.stats()
    assert stats["claims"] == 2
    assert stats["quarantined"] == 1
    assert stats["released"] == 1
    by_id = {c.claim_id: c for c in device.memory()}
    assert by_id[held.claim_id].is_quarantined()
    assert by_id[released.claim_id].was_released()


def test_an_entitlement_floor_holds_below_its_rank(open_device):
    """The other way of naming an untrustworthy source: a number, not a set."""
    device = open_device(
        trust=TrustPolicy(policy_version="floor-1", entitlement_floor=50)
    )
    held = device.record(claim(source_class=AuthorityClass.THIRD_PARTY_FEED))
    let_through = device.record(
        claim(value="intact", source_class=AuthorityClass.UNATTESTED_HUMAN)
    )

    assert held.is_quarantined()
    assert "ranks 30" in held.quarantine_reason
    assert "below the entitlement floor 50" in held.quarantine_reason
    assert not let_through.is_quarantined()
    assert device.stats()["trust_entitlement_floor"] == 50


def test_a_verdict_survives_plain_data_with_the_corroboration_on_it(device):
    """The analysis reaches a caller that only has JSON."""
    import json

    held = [
        device.record(rumour(observer=f"board-{i}")).claim_id for i in range(2)
    ]
    device.release(held, reason="released on the operator's word")

    payload = json.loads(json.dumps(ask(device).to_payload()))
    assert payload["claims"][0]["corroboration"]["self_corroborated"] is True
    assert payload["claims"][0]["corroboration"]["classes"] == [
        [AuthorityClass.RUMOUR.value, 2]
    ]
    assert payload["claims"][0]["corroboration"]["echoes"] == 1
    assert payload["claims"][0]["corroborations"] == 2