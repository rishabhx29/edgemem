"""Cold-chain telematics: refrigerated trailers running in and out of coverage.

The vertical the engine is demonstrated against, and the one the existing
fixture corpus is written in. A driver in a dead zone keeps working and keeps
recording; what reaches the depot is decided claim by claim, and a seal that two
people saw differently is a disagreement rather than a merge.

Everything industry-shaped lives in this file, which is the point: the engine
holds none of it, and ``tests/test_schema_pack.py`` reads the engine's source to
prove it.

``VOCABULARY`` is this vertical's own list of the words that would give it away.
It is declared here, next to the vocabulary, so the test that checks the engine
carries none of it has a list that cannot silently fall out of date with the
pack it belongs to.
"""

from __future__ import annotations

from edgemem.domain import AuthorityClass, Ladder
from edgemem.schema import Labels, SchemaPack, SubjectModel

VOCABULARY = frozenset(
    {
        "as-built",
        "axle",
        "carrier",
        "chill",
        "cold-chain",
        "coldchain",
        "consignee",
        "consignor",
        "detention",
        "dispatcher",
        "dock",
        "food",
        "freezer",
        "halvorsen",
        "hours-of-service",
        "larder",
        "pallet",
        "reefer",
        "sanitary",
        "subpart",
        "t-114",
        "telematics",
        "tractor",
        "trailer",
        "trk-7",
        "transportation",
        "395.8",
        "duty-log",
    }
)
"""Words that belong to this vertical and to no other."""

PACK = SchemaPack(
    labels=Labels(
        claim="load observation",
        attribute="seal condition",
        conflict="load disagreement",
    ),
    ladder=Ladder(
        version="coldchain-2026.1",
        entries=(
            (AuthorityClass.INSTRUMENT, 100),
            (AuthorityClass.ATTESTED_HUMAN, 80),
            (AuthorityClass.UNATTESTED_HUMAN, 50),
            (AuthorityClass.THIRD_PARTY_FEED, 30),
            (AuthorityClass.RUMOUR, 10),
        ),
        note=(
            "A calibrated dock scanner outranks a sign-off, which outranks a "
            "handwritten note. Presented for a person to settle: the device "
            "does not choose between disagreeing claims, and a later claim is "
            "not a truer one."
        ),
    ),
    subject_model=SubjectModel(
        id_label="trailer number",
        default_attribute="state",
        attributes={
            "state": "seal condition",
            "temperature": "set-point temperature",
            "door": "door state",
        },
    ),
    citation="21 CFR Part 1 Subpart O §1.908",
)

DEFAULT = PACK
"""The vertical the engine's own default reproduces.

Not an alias of the engine's unbound pack — the engine cannot know a vertical —
but the pack whose data every existing assertion is written against. A test
binds this one explicitly and checks the engine answers exactly as it does
unbound, so the compatibility is demonstrated rather than assumed.
"""
