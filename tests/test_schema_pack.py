"""Binding a second vertical by supplying data.

A vertical binds by handing the engine four things — display labels, an
authority ladder, a subject model and a regulatory citation — and by nothing
else. This file holds the two bindings and the tests that make the claim
structural rather than asserted:

- a substation torque dispute that runs end to end through ``ask`` and comes
  back conflicted against *its own* ladder, sealed local by its own sensitivity,
  and cited against its own regulation;
- a scan of the engine's source that finds no word belonging to any industry;
- a check that a schema pack supplies nothing but data, so a new vertical cannot
  be a new branch in the engine.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path
from types import MappingProxyType

import pytest

import edgemem
from edgemem.domain import AuthorityClass, Claim, Residency, utcnow
from edgemem.memory import EdgeMemory, Ladder, default_ladder
from edgemem.schema import (
    DEFAULT_PACK,
    UNBOUND_CITATION,
    Labels,
    SchemaPack,
    SubjectModel,
)
from edgemem.store import ShardStore
from fixtures import substation, trucks

ENGINE_ROOT = Path(edgemem.__file__).resolve().parent
"""The engine's own directory, resolved from the imported package.

Resolved rather than written down, so the vocabulary scan below cannot be
satisfied by reading some other copy of the source.
"""


def open_device(path, device_id: str, **kwargs) -> EdgeMemory:
    """A device on its own shard directory, torn down by the caller."""
    store = ShardStore(path, device_id=device_id)
    store.open()
    return EdgeMemory(store, **kwargs)


@pytest.fixture
def device(tmp_path):
    mem = open_device(tmp_path / "unbound", device_id="TRK-7")
    yield mem
    mem.store.close()
    mem.store.destroy()


@pytest.fixture
def substation_device(tmp_path):
    mem = open_device(tmp_path / "plant", device_id="TECH-2", pack=substation.PACK)
    yield mem
    mem.store.close()
    mem.store.destroy()


# --------------------------------------------------------------------------
# two verticals, one engine
# --------------------------------------------------------------------------


def test_two_verticals_bind_to_one_engine():
    assert trucks.PACK is not substation.PACK
    assert trucks.PACK.citation != substation.PACK.citation
    assert trucks.PACK.ladder.version != substation.PACK.ladder.version
    assert trucks.PACK.labels != substation.PACK.labels
    assert trucks.PACK.subject_model != substation.PACK.subject_model


def test_a_pack_supplies_only_data():
    """Binding a vertical must not be a way to add behaviour to the engine.

    A pack instance exposes its four fields and nothing else, and the class
    defines no public callable for the engine to reach into. If a pack could
    hold a callback, every new vertical would be a new branch in the engine —
    which is the one change this design refuses to make.
    """
    assert {f.name for f in fields(SchemaPack)} == {
        "labels",
        "ladder",
        "subject_model",
        "citation",
    }

    own_public = {
        name: value
        for name, value in vars(SchemaPack).items()
        if not name.startswith("_")
    }
    assert not own_public, f"SchemaPack gained public names: {sorted(own_public)}"
    assert not [n for n, v in own_public.items() if callable(v)]

    for pack in (trucks.PACK, substation.PACK, DEFAULT_PACK):
        exposed = {name for name in dir(pack) if not name.startswith("_")}
        assert exposed == {f.name for f in fields(pack)}, (
            f"{pack.citation!r} exposes more than its data"
        )
        for field in fields(pack):
            value = getattr(pack, field.name)
            assert not callable(value), f"{field.name} is behaviour, not data"
            # Exactly the four declared data types, by identity. Not a subclass
            # carrying extra behaviour, and not an arbitrary object the engine
            # would have to understand in order to use.
            assert type(value) in (Labels, Ladder, SubjectModel, str), (
                f"{field.name} is not one of the four data types"
            )

    assert isinstance(trucks.PACK.subject_model.attributes, MappingProxyType)
    assert trucks.PACK.labels == Labels(
        claim="load observation",
        attribute="seal condition",
        conflict="load disagreement",
    )


def test_a_pack_cannot_be_mutated_behind_a_verdict():
    """What a pack said when a verdict was rendered is what it still says."""
    attributes = {"state": "seal condition"}
    pack = SchemaPack(
        labels=trucks.PACK.labels,
        ladder=trucks.PACK.ladder,
        subject_model=SubjectModel(
            id_label="trailer number",
            default_attribute="state",
            attributes=attributes,
        ),
        citation=trucks.PACK.citation,
    )

    attributes["state"] = "something else"

    assert pack.subject_model.attributes["state"] == "seal condition"
    with pytest.raises(TypeError):
        pack.subject_model.attributes["state"] = "and again"


def test_an_unbound_device_claims_no_regulation(device):
    """The engine has no opinion about what governs somebody's work.

    It has no regulation to borrow either, so a device that was never told
    which vertical it is in says so rather than asserting a citation.
    """
    assert DEFAULT_PACK.citation == UNBOUND_CITATION
    assert DEFAULT_PACK.citation not in {trucks.PACK.citation, substation.PACK.citation}
    assert device.ask("what happened here", subject="nothing").citation == (
        UNBOUND_CITATION
    )


# --------------------------------------------------------------------------
# the trucks pack is the vertical the existing corpus already describes
# --------------------------------------------------------------------------

TRUCK_SUBJECT = "trailer T-114 seal"


def truck_claim(**over) -> Claim:
    now = utcnow()
    base = {
        "subject": TRUCK_SUBJECT,
        "attribute": "state",
        "value": "broken",
        "author": "operator-7",
        "observer": "handheld",
        "device_id": "TRK-7",
        "observed_at": now,
        "recorded_at": now,
    }
    base.update(over)
    return Claim(**base)


def test_binding_the_trucks_pack_changes_no_verdict_the_existing_suite_asserts(tmp_path):
    """Same claims, same store, one device bound and one not.

    Compared side by side rather than against remembered values, so the check
    is about the binding and not about the corpus.
    """
    store = ShardStore(tmp_path / "mutable", device_id="TRK-7")
    store.open()
    try:
        bound = EdgeMemory(store, pack=trucks.PACK)
        unbound = EdgeMemory(store)

        left = truck_claim(value="broken")
        left.causal.observe("TRK-7")
        right = truck_claim(value="intact", observer="dock-scanner", device_id="DEPOT-1")
        right.causal.observe("DEPOT-1")
        bound.record_many([left, right])

        after = bound.ask("seal state", subject=TRUCK_SUBJECT)
        before = unbound.ask("seal state", subject=TRUCK_SUBJECT)
    finally:
        store.close()
        store.destroy()

    assert after.kind == before.kind
    assert [s.claim.value for s in after.conflicts[0]] == [
        s.claim.value for s in before.conflicts[0]
    ]
    assert [s.claim.residency for s in after.conflicts[0]] == [
        s.claim.residency for s in before.conflicts[0]
    ]
    assert after.authority.ordered_classes == before.authority.ordered_classes
    assert after.authority.entitling_class == before.authority.entitling_class

    # Only the vertical's own words and its own regulation differ.
    assert after.citation == trucks.PACK.citation
    assert before.citation == UNBOUND_CITATION
    assert after.summary != before.summary
    assert trucks.PACK.labels.conflict in after.summary
    assert DEFAULT_PACK.labels.conflict in before.summary


def test_the_trucks_pack_orders_authority_as_the_engine_default_does():
    assert trucks.PACK.ladder.entries == default_ladder()
    assert [c.value for c, _ in trucks.PACK.ladder.entries] == [
        c.value for c, _ in default_ladder()
    ]


# --------------------------------------------------------------------------
# the substation scenario: a confidential local deviation
# --------------------------------------------------------------------------

SUBJECT = "substation 4 breaker CB-7"
SPECIFICATION = "torque specification"
SITE_STANDARD = "165 Nm (per site standard SS-14, supersedes manual)"
OEM_FIGURE = "180 Nm (per OEM bulletin OB-4471)"


def site_standard() -> Claim:
    """The signed local deviation, written on the technician's own tablet."""
    now = utcnow()
    claim = Claim(
        subject=SUBJECT,
        attribute=SPECIFICATION,
        value=SITE_STANDARD,
        author="shift supervisor R. Okonkwo",
        observer="tablet TC-9",
        device_id="TECH-2",
        observed_at=now,
        recorded_at=now,
        source_class=AuthorityClass.ATTESTED_HUMAN,
        sensitivity=0.93,
        salience=0.8,
        urgency=0.9,
    )
    claim.causal.observe("TECH-2")
    return claim


def oem_specification() -> Claim:
    """The manufacturer's figure, relayed by the central office.

    A third-party feed rather than an instrument: nobody at the depot observed
    the specification, they forwarded a document.
    """
    now = utcnow()
    claim = Claim(
        subject=SUBJECT,
        attribute=SPECIFICATION,
        value=OEM_FIGURE,
        author="OEM bulletin OB-4471",
        observer="central specification service",
        device_id="DEPOT-1",
        observed_at=now,
        recorded_at=now,
        source_class=AuthorityClass.THIRD_PARTY_FEED,
        sensitivity=0.0,
        salience=0.7,
        urgency=0.6,
    )
    claim.causal.observe("DEPOT-1")
    return claim


def ask_specification(mem: EdgeMemory):
    return mem.ask("what tightening specification applies to CB-7", subject=SUBJECT)


def test_the_two_specifications_come_back_as_a_conflict(substation_device):
    """Written apart, neither having seen the other. Neither is discarded."""
    substation_device.record_many([site_standard(), oem_specification()])

    verdict = ask_specification(substation_device)

    assert verdict.kind.value == "CONFLICTED"
    assert len(verdict.conflicts) == 1
    left, right = verdict.conflicts[0]
    assert {left.claim.value, right.claim.value} == {SITE_STANDARD, OEM_FIGURE}
    assert {left.claim.author, right.claim.author} == {
        "shift supervisor R. Okonkwo",
        "OEM bulletin OB-4471",
    }
    assert "not choosing" in verdict.summary.lower()
    assert substation.PACK.labels.conflict in verdict.summary


def test_the_conflict_presents_the_substation_ladder_not_the_default(substation_device):
    """The escalation must show the vertical's ordering, not the engine's."""
    substation_device.record_many([site_standard(), oem_specification()])

    verdict = ask_specification(substation_device)

    assert verdict.authority is not None
    assert verdict.authority.ladder_version == substation.PACK.ladder.version
    assert verdict.authority.ladder_version != Ladder().version
    assert verdict.authority.ordered_classes == substation.PACK.ladder.ordered()
    assert verdict.authority.ordered_classes != Ladder().ordered()
    assert verdict.authority.note == substation.PACK.ladder.note
    assert verdict.authority.entitling_class == AuthorityClass.ATTESTED_HUMAN.value


def test_the_same_claims_are_entitled_differently_under_each_vertical_ladder(tmp_path):
    """A wrench reading attests what was applied, not what was required.

    The two orderings name different sides of the identical disagreement as
    entitled to settle it. Neither resolves anything — that is the same in both
    — so what differs is only what the escalation shows the person settling it.
    """
    signed = site_standard()
    measured = Claim(
        subject=SUBJECT,
        attribute=SPECIFICATION,
        value="90 Nm applied",
        author="technician L. Byrne",
        observer="calibrated torque wrench TW-2",
        device_id="TECH-9",
        observed_at=utcnow(),
        recorded_at=utcnow(),
        source_class=AuthorityClass.INSTRUMENT,
    )
    measured.causal.observe("TECH-9")

    store = ShardStore(tmp_path / "mutable", device_id="TECH-2")
    store.open()
    try:
        by_substation = None
        by_default = None
        for pack, name in ((substation.PACK, "substation"), (None, "default")):
            mem = EdgeMemory(store, pack=pack)
            mem.record_many([signed, measured])
            authority = ask_specification(mem).authority
            if name == "substation":
                by_substation = authority
            else:
                by_default = authority
    finally:
        store.close()
        store.destroy()

    assert by_substation.entitling_class == AuthorityClass.ATTESTED_HUMAN.value
    assert by_default.entitling_class == AuthorityClass.INSTRUMENT.value
    assert by_substation.ordered_classes != by_default.ordered_classes


def test_the_site_standard_stays_local_and_says_why(substation_device):
    """The deviation is a confidential local matter. It must not reach the depot.

    Sensitivity is a veto rather than a weight, so the claim's own urgency —
    which would otherwise carry it — does not move it, and the reason a caller
    reads names the veto instead of leaving it to be inferred.
    """
    sealed = site_standard()
    substation_device.record_many([sealed, oem_specification()])

    reason = substation_device.residency_of(sealed)
    assert reason.residency is Residency.LOCAL
    assert reason.vetoed_by == "sensitivity"
    assert "vetoed by sensitivity" in reason.render()

    verdict = ask_specification(substation_device)
    sides = {side.claim.value: side.claim for side in verdict.conflicts[0]}
    cited = sides[SITE_STANDARD]
    assert cited.residency == "LOCAL"
    assert "vetoed by sensitivity" in cited.reason
    assert "sensitivity 0.93 >= 0.80" in cited.reason
    # The urgency that would have carried it is on the record anyway.
    assert "urgency 0.90 >= 0.50" in cited.reason


def test_the_oem_figure_is_the_side_the_central_office_already_raised(substation_device):
    """The other side is not sealed: the office supplied it, so it may travel."""
    forwardable = oem_specification()
    substation_device.record_many([site_standard(), forwardable])

    reason = substation_device.residency_of(forwardable)
    assert reason.residency is Residency.SYNC
    assert reason.vetoed_by is None


def test_the_verdict_cites_the_substation_regulation(substation_device):
    substation_device.record_many([site_standard(), oem_specification()])
    verdict = ask_specification(substation_device)

    assert verdict.citation == "21 CFR Part 11 §11.10(e)"
    assert verdict.citation == substation.PACK.citation
    assert verdict.citation != trucks.PACK.citation
    assert verdict.citation != UNBOUND_CITATION
    assert verdict.to_payload()["citation"] == verdict.citation


def test_hours_of_service_is_a_category_error_and_never_appears(substation_device):
    """Duty-log regulation governs driver duty records, not site work.

    Citing it for a tightening specification would be borrowing another
    vertical's regulation because it was the one already in the codebase.
    """
    substation_device.record_many([site_standard(), oem_specification()])
    verdict = ask_specification(substation_device)

    rendered = f"{verdict.citation} {verdict.summary} {verdict.authority.note}"
    for forbidden in ("395", "hours", "duty", "cold-chain", "trailer", "seal"):
        assert forbidden not in rendered.lower(), (
            f"{forbidden!r} has no business in a substation verdict"
        )


def test_the_citation_travels_on_every_kind_of_verdict(substation_device, tmp_path):
    """A regulated record carries its provision whichever outcome came back."""
    nothing_known = substation_device.ask("what happened to the plant today")
    assert nothing_known.kind.value == "UNRESOLVED_CLOUD_REQUIRED"
    assert nothing_known.citation == substation.PACK.citation

    substation_device.record_many([site_standard(), oem_specification()])
    conflicted = ask_specification(substation_device)
    assert conflicted.citation == substation.PACK.citation

    alone = open_device(tmp_path / "single", device_id="TECH-3", pack=substation.PACK)
    try:
        alone.record(oem_specification())
        assert ask_specification(alone).citation == substation.PACK.citation
    finally:
        alone.store.close()
        alone.store.destroy()


def test_a_question_naming_no_known_subject_is_answered_in_the_verticals_words(
    device, substation_device
):
    """The caller reads the vertical's language, including about its own blanks."""
    bound = substation_device.ask("what is the state of things here")
    unbound = device.ask("what is the state of things here")

    assert substation.PACK.labels.claim in bound.summary
    assert substation.PACK.subject_model.id_label in bound.summary
    assert DEFAULT_PACK.labels.claim in unbound.summary
    assert DEFAULT_PACK.subject_model.id_label in unbound.summary
    assert bound.summary != unbound.summary


def test_the_substation_labels_reach_the_answers_they_name(substation_device, tmp_path):
    alone = open_device(tmp_path / "single", device_id="TECH-3", pack=substation.PACK)
    try:
        alone.record(oem_specification())
        verdict = ask_specification(alone)

        assert verdict.kind.value == "ANSWERED_LOCALLY"
        assert substation.PACK.subject_model.attributes[SPECIFICATION] in (
            verdict.summary
        )
        assert "load observation" not in verdict.summary
        assert "conflict" not in verdict.summary.lower()
    finally:
        alone.store.close()
        alone.store.destroy()


def test_a_vague_question_in_a_bound_vertical_falls_back_to_its_default(tmp_path):
    """A bare question in this vertical is about its own default attribute.

    With nothing bound there is no default, so the answer is left unnarrowed —
    which is why the two devices differ on the very same vague question.
    """
    now = utcnow()

    def reading(attribute: str, value: str) -> Claim:
        return Claim(
            subject=SUBJECT,
            attribute=attribute,
            value=value,
            author="R. Okonkwo",
            observer="tablet TC-9",
            device_id="TECH-2",
            observed_at=now,
            recorded_at=now,
        )

    store = ShardStore(tmp_path / "mutable", device_id="TECH-2")
    store.open()
    try:
        bound = EdgeMemory(store, pack=substation.PACK)
        bound.record_many(
            [
                reading(SPECIFICATION, "165 Nm"),
                reading("insulation resistance", "18 Mohm"),
            ]
        )
        unbound = EdgeMemory(store)

        vague = "what is the state of CB-7"
        narrowed = bound.ask(vague, subject=SUBJECT)
        unnarrowed = unbound.ask(vague, subject=SUBJECT)
    finally:
        store.close()
        store.destroy()

    assert [c.attribute for c in narrowed.claims] == [SPECIFICATION]
    assert len(unnarrowed.claims) == 2


# --------------------------------------------------------------------------
# the engine holds no industry's vocabulary
# --------------------------------------------------------------------------

TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")


def tokens(text: str) -> set[str]:
    return set(TOKEN.findall(text.lower()))


def vocabulary_in(text: str, vocabulary) -> set[str]:
    return tokens(text) & set(vocabulary)


def engine_modules() -> list[Path]:
    """Every module the engine ships, read from disk rather than from a list.

    A list would go stale the moment the engine grew a file, and a stale list is
    the usual way a "no industry vocabulary" test quietly stops meaning anything.
    """
    return sorted(
        path
        for path in ENGINE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def forbidden_vocabulary() -> frozenset[str]:
    """Every word the two bound verticals own.

    Read from the packs rather than written here, so a pack cannot gain a word
    without the net that watches the engine gaining it too.
    """
    return trucks.VOCABULARY | substation.VOCABULARY


def test_the_net_is_worth_having():
    """A vocabulary list that found nothing would pass every scan below."""
    vocabulary = forbidden_vocabulary()
    assert len(vocabulary) >= 40
    assert "trailer" in vocabulary
    assert "torque" in vocabulary


def test_the_detector_finds_vocabulary_where_it_is_supposed_to():
    """The negative results below mean nothing unless this passes.

    The same tokeniser and the same word list, pointed at text that certainly
    contains the words. If either were broken, every engine scan would pass for
    the wrong reason — which is the failure mode a "no industry words" test has
    when nobody checks that it can still see.
    """
    vocabulary = forbidden_vocabulary()

    for pack in (trucks, substation):
        found = vocabulary_in(
            Path(pack.__file__).read_text(encoding="utf-8"), vocabulary
        )
        # Every word a pack declares is a word that pack actually uses, so the
        # list cannot rot into a list of words nobody wrote.
        stale = set(pack.VOCABULARY) - found
        assert not stale, f"{pack.__name__} declares words it never uses: {stale}"
        assert len(found) >= 20, f"the detector found almost nothing in {pack.__name__}"

    trucks_source = Path(trucks.__file__).read_text(encoding="utf-8")
    substation_source = Path(substation.__file__).read_text(encoding="utf-8")
    assert "reefer" in vocabulary_in(trucks_source, vocabulary)
    assert "torque" in vocabulary_in(substation_source, vocabulary)
    assert vocabulary_in("a reefer trailer with a broken seal", vocabulary) == {
        "reefer",
        "trailer",
    }
    assert vocabulary_in("a breaker torqued past torque spec", vocabulary) == {
        "breaker",
        "torque",
    }


def test_no_engine_module_carries_an_industry_word():
    """Domain-agnosticism as a structure, not a claim.

    Every module the engine ships is read and searched for the words the two
    bound verticals own. A hit would mean the engine had grown a branch for
    somebody's industry, which is the one change this design refuses to make.
    """
    vocabulary = forbidden_vocabulary()
    modules = engine_modules()
    assert len(modules) >= 5, f"no engine modules found under {ENGINE_ROOT}"

    for module in modules:
        hits = vocabulary_in(module.read_text(encoding="utf-8"), vocabulary)
        assert not hits, f"{module.name} carries {sorted(hits)}"


def test_the_vocabulary_scan_reaches_every_module_the_engine_has():
    """A subpackage cannot hide a word from the scan above.

    Read recursively and compared against what is actually on disk, so adding a
    directory to the engine does not quietly narrow what is checked.
    """
    modules = engine_modules()
    on_disk = {
        path
        for path in ENGINE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    }

    assert len(modules) == len(on_disk)
    assert len(on_disk) >= 5
    assert not list(ENGINE_ROOT.glob("*/__init__.py")), (
        "the engine has gained a subpackage; widen the scan before trusting it"
    )
