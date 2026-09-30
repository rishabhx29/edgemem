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
    Corroboration,
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
from edgemem.trust import DEFAULT_TRUST_POLICY, TrustDecision, TrustPolicy

__all__ = [
    "AuthorityClass",
    "AuthorityView",
    "CausalContext",
    "CitedClaim",
    "Claim",
    "ConflictSide",
    "Corroboration",
    "DEFAULT_PACK",
    "DEFAULT_TRUST_POLICY",
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
    "TrustDecision",
    "TrustPolicy",
    "UNBOUND_CITATION",
    "VendorSnapshotUnavailable",
    "Verdict",
    "VerdictKind",
    "default_ladder",
]
