"""Substation torque specifications: a technician alone in a plant basement.

The same shape of problem as a truck in a dead zone, with none of the
vocabulary in common. A breaker has an OEM tightening specification and a site
standard that supersedes it; both are true of different documents, the
technician's tablet holds the signed site standard, and the deviation is a
confidential local matter the central office must not learn about until somebody
has reviewed it.

Two things in this pack are worth reading closely, because they are the vertical
speaking and not the engine:

- **The ladder puts the signed standard above the instrument.** The question is
  what a fastener *should* be tightened to, and no instrument observes a
  specification. A calibrated wrench reading attests what was applied, not what
  was required, so for this vertical the signed site standard is the highest
  entitlement present. The same two claims under the default ordering would name
  the other side as entitling. Neither ordering resolves anything; they differ
  only in what the escalation shows whoever settles it.
- **The citation is the electronic-records provision**, which requires that a
  change not obscure what was previously recorded and that signatures be
  attributed. Hours-of-service duty-log regulation is a category error here: it
  governs driver duty records, says nothing about site work, and is named in
  this module only so a test can prove the engine never reached for it.
"""

from __future__ import annotations

from edgemem.domain import AuthorityClass, Ladder
from edgemem.schema import Labels, SchemaPack, SubjectModel

VOCABULARY = frozenset(
    {
        "as-built",
        "basement",
        "breaker",
        "bushing",
        "busbar",
        "cb-7",
        "concession",
        "gland",
        "newton",
        "nonconformance",
        "ob-4471",
        "oem",
        "recloser",
        "ss-14",
        "substation",
        "switchgear",
        "torque",
        "transformer",
        "winding",
        "wrench",
    }
)
"""Words that belong to this vertical and to no other."""

PACK = SchemaPack(
    labels=Labels(
        claim="site reading",
        attribute="specified value",
        conflict="deviation",
    ),
    ladder=Ladder(
        version="substation-2026.1",
        entries=(
            (AuthorityClass.ATTESTED_HUMAN, 100),
            (AuthorityClass.INSTRUMENT, 80),
            (AuthorityClass.UNATTESTED_HUMAN, 55),
            (AuthorityClass.THIRD_PARTY_FEED, 45),
            (AuthorityClass.RUMOUR, 5),
        ),
        note=(
            "A signed site standard outranks a wrench reading here, because a "
            "wrench attests what was applied and the question is what was "
            "required. Presented for a person to settle: the tablet does not "
            "choose between disagreeing readings, and the later one is not "
            "therefore the truer one."
        ),
    ),
    subject_model=SubjectModel(
        id_label="breaker tag",
        default_attribute="torque specification",
        attributes={
            "torque specification": "tightening specification",
            "torque applied": "tightening value",
            "insulation resistance": "insulation resistance",
        },
    ),
    citation="21 CFR Part 11 §11.10(e)",
)
