"""Persisted statements for the M1 institutional compliance profile."""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Protocol

from .model import Evidence


COMPLIANCE_PROFILE = "institutional-compliance-v1"


@dataclass(frozen=True)
class Claim:
    id: str
    version_id: str
    profile: str
    text: str
    evidence: tuple[Evidence, ...]


def compliance_claim(version_id: str, text: str, evidence: tuple[Evidence, ...]) -> Claim:
    references = tuple(sorted(set(evidence), key=lambda item: item.id))
    identity = json.dumps(
        [version_id, COMPLIANCE_PROFILE, text, [item.id for item in references]],
        ensure_ascii=False,
    )
    return Claim(sha256(identity.encode("utf-8")).hexdigest(), version_id, COMPLIANCE_PROFILE,
                 text, references)


class ClaimStore(Protocol):
    def save_claims(self, claims: tuple[Claim, ...]) -> None: ...
    def claims(self, version_id: str) -> tuple[Claim, ...]: ...
