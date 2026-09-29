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
    NeededClaim,
    Residency,
    ResidencyReason,
    Trust,
    Verdict,
    VerdictKind,
)

__all__ = [
    "AuthorityClass",
    "AuthorityView",
    "CausalContext",
    "CitedClaim",
    "Claim",
    "ConflictSide",
    "NeededClaim",
    "Residency",
    "ResidencyReason",
    "Trust",
    "Verdict",
    "VerdictKind",
]
