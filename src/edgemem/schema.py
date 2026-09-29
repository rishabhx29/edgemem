"""Schema packs: how a vertical binds to the engine without changing it.

The engine's only concepts are claim, subject, attribute, conflict, verdict,
residency and source class. A vertical is not a fifth concept — it is a
different set of *words* for those concepts, a different ordering of who may
settle a disagreement, a different model of what a subject is, and a different
regulatory anchor. Those four things are a :class:`SchemaPack`.

A pack is **data**. It defines no methods, carries no callbacks, and the engine
never inspects it for anything but the four fields below. That is the whole
point: if a pack could hold behaviour, a new vertical would be a new branch in
the engine, and "domain-agnostic" would be a claim rather than a structure. The
test that reads this file and refuses to find an industry word in it is the same
argument made about the rest of the engine.

Three of the four are consumed by the engine:

- ``labels`` are substituted into the sentences a verdict is summarised in, so
  the words a caller reads are the vertical's own.
- ``ladder`` is what an escalated conflict is presented against.
- ``subject_model.default_attribute`` narrows a question that names no aspect,
  and ``subject_model.attributes`` names the aspects it does.
- ``citation`` travels on every verdict, so a regulated record carries the
  provision it is being kept under rather than a regulation the engine guessed.

Residency policy is deliberately *not* here. Whether a claim may leave the
device is a property of the device and the operator running it, not of the
industry, so two verticals running the same policy produce the same residency
decisions for the same claim shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from edgemem.domain import Ladder

UNBOUND_CITATION = "unbound: no vertical is bound, so no regulatory anchor is claimed"
"""What a device with no pack says in place of a citation.

The engine has no opinion about which regulation governs somebody's work, and
inventing one would be the same category error as borrowing another vertical's.
A device that has not been told does not get to assert.
"""


@dataclass(frozen=True)
class Labels:
    """The vertical's own words for the engine's concepts."""

    claim: str
    """What the vertical calls an attributed assertion."""

    attribute: str
    """What the vertical calls the aspect of a subject being asserted."""

    conflict: str
    """What the vertical calls two assertions that cannot both be true."""


@dataclass(frozen=True)
class SubjectModel:
    """What a subject is in this vertical.

    A subject is a string to the engine. This is how the vertical says which
    string, and what the engine should make of it when a question is vague.
    """

    id_label: str
    """What the vertical calls the identifier a subject is named by."""

    default_attribute: str
    """The attribute a question covers when it names none.

    Empty when no vertical is bound: with nothing bound the engine narrows an
    ambiguous question to nothing, which is the safe direction.
    """

    attributes: Mapping[str, str] = field(default_factory=dict)
    """Engine attribute key -> the vertical's display name for that attribute.

    The key is the engine's; only the display name is the vertical's, so the
    key a claim carries stays comparable across verticals.
    """


@dataclass(frozen=True)
class SchemaPack:
    """Everything a vertical supplies. Four fields, all of them data.

    Frozen and method-free on purpose. There is no code path in the engine that
    asks a pack what to do, because a pack has nothing to do.
    """

    labels: Labels
    ladder: Ladder
    subject_model: SubjectModel
    citation: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_model",
            _with_frozen_mapping(self.subject_model),
        )


def _with_frozen_mapping(model: SubjectModel) -> SubjectModel:
    """Freeze the attribute map in place of a caller-supplied dict.

    A pack is a promise that what it says will not move under a verdict, so the
    one mutable value it can hold is made immutable here rather than trusted.
    """
    frozen = MappingProxyType(dict(model.attributes))
    return SubjectModel(
        id_label=model.id_label,
        default_attribute=model.default_attribute,
        attributes=frozen,
    )


DEFAULT_PACK = SchemaPack(
    labels=Labels(claim="claim", attribute="attribute", conflict="conflict"),
    ladder=Ladder(),
    subject_model=SubjectModel(id_label="subject", default_attribute=""),
    citation=UNBOUND_CITATION,
)
"""What runs when no vertical is bound.

Deliberately opinionless: the engine's own words, no ladder beyond the default
ordering, no aspect to fall back on, and no regulation. A deployment that has
not chosen a vertical should not look like one that has.
"""
