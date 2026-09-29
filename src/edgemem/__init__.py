"""Disagreement-durable edge memory engine.

A claim is an attributed assertion, never an overwritten fact. Residency
decisions carry their reasoning. Conflicts escalate rather than resolve.

The engine is domain-agnostic: it knows only claims, subjects, attributes,
conflicts, verdicts, residency and source classes. A vertical binds to it
through a schema pack.
"""

from edgemem.domain import (
    AuthorityClass,
    AuthorityView,
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
)
from edgemem.outbox import Outbox
from edgemem.schema import (
    DEFAULT_PACK,
    UNBOUND_CITATION,
    Labels,
    SchemaPack,
    SubjectModel,
)
from edgemem.sync import (
    Depot,
    DeviceLink,
    ProtocolError,
    SyncPath,
    SyncReport,
    VendorSnapshotUnavailable,
)

__all__ = [
    "AuthorityClass",
    "AuthorityView",
    "CausalContext",
    "CitedClaim",
    "Claim",
    "ConflictSide",
    "DEFAULT_PACK",
    "Depot",
    "DeviceLink",
    "Ladder",
    "Labels",
    "NeededClaim",
    "Outbox",
    "ProtocolError",
    "Residency",
    "ResidencyReason",
    "SchemaPack",
    "SubjectModel",
    "SyncPath",
    "SyncReport",
    "Trust",
    "UNBOUND_CITATION",
    "VendorSnapshotUnavailable",
    "Verdict",
    "VerdictKind",
    "default_ladder",
]
