from dataclasses import dataclass
from typing import Literal, Mapping, Protocol


@dataclass(frozen=True)
class Block:
    block_key: str
    heading_path: tuple[str, ...]
    ordinal: int
    start_offset: int
    end_offset: int
    text: str


@dataclass(frozen=True)
class Version:
    version_id: str
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class Document:
    document_id: str
    version: Version


@dataclass(frozen=True)
class Corpus:
    before: tuple[Document, ...]
    after: tuple[Document, ...]


@dataclass(frozen=True)
class ArtifactItem:
    item_id: str
    snapshot_text: str


@dataclass(frozen=True)
class Budget:
    max_steps: int
    max_tokens: int
    max_wall_seconds: float


@dataclass(frozen=True)
class ReviewRequest:
    corpus: Corpus
    artifact: tuple[ArtifactItem, ...]
    goal: str
    budget: Budget
    ledger: Mapping[str, object] | None = None


@dataclass(frozen=True)
class EvidenceReference:
    document_id: str
    version_id: str
    block_key: str


@dataclass(frozen=True)
class Proposal:
    item_id: str
    judgment: Literal["material", "immaterial", "not_affected"]
    revised_text: str | None = None
    evidence: tuple[EvidenceReference, ...] = ()


@dataclass(frozen=True)
class Cost:
    input_tokens: int | None
    output_tokens: int | None
    estimated_amount: float | None
    model_id: str | None


@dataclass(frozen=True)
class Timing:
    model_seconds: float | None
    total_seconds: float


@dataclass(frozen=True)
class ReviewResult:
    proposals: tuple[Proposal, ...]
    cost: Cost
    timing: Timing
    state: Literal["done", "paused", "failed"]
    used_ledger: bool
    gaps: tuple[str, ...] = ()
    error: str | None = None


class ReviewSystem(Protocol):
    def review(self, request: ReviewRequest) -> ReviewResult: ...
