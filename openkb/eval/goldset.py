import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import ArtifactItem, Block, Budget, Corpus, Document, ReviewRequest, Version


@dataclass(frozen=True)
class GoldCase:
    case_id: str
    request: ReviewRequest
    annotations: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class GoldSet:
    profile_version: str
    goldset_hash: str
    cases: tuple[GoldCase, ...]


def _snapshot(documents: list[dict[str, Any]], location: str) -> tuple[Document, ...]:
    snapshot = []
    for document in documents:
        version_data = document["version"]
        blocks = []
        for index, block in enumerate(version_data["blocks"]):
            if not isinstance(block, dict):
                raise ValueError(f"{location}, block {index}: expected a JSON object")
            block_key = block.get("block_key")
            if not isinstance(block_key, str) or not block_key.strip():
                raise ValueError(
                    f"{location}, document {document['document_id']!r}, block {index}: "
                    "block_key must be a non-empty string"
                )
            blocks.append(Block(
                block_key=block_key,
                heading_path=tuple(block["heading_path"]),
                ordinal=block["ordinal"],
                start_offset=block["start_offset"],
                end_offset=block["end_offset"],
                text=block["text"],
            ))
        version = Version(version_data["version_id"], tuple(blocks))
        snapshot.append(Document(document["document_id"], version))
    return tuple(snapshot)


def _revision_documents(
    documents: tuple[Document, ...], location: str,
) -> dict[str, Version]:
    by_id = {}
    for document in documents:
        if document.document_id in by_id:
            raise ValueError(f"{location}: duplicate document_id {document.document_id!r}")
        by_id[document.document_id] = document.version
        keys = {block.block_key for block in document.version.blocks}
        if len(keys) != len(document.version.blocks):
            raise ValueError(f"{location}: duplicate block_key in {document.document_id!r}")
    return by_id


def _validate_revision(corpus: Corpus, case_id: str) -> None:
    location = f"case {case_id!r}: invalid revision pair"
    before = _revision_documents(corpus.before, f"{location} at corpus.before")
    after = _revision_documents(corpus.after, f"{location} at corpus.after")
    if not before or before.keys() != after.keys():
        raise ValueError(f"{location}: both snapshots must contain the same document IDs")
    for document_id, old in before.items():
        new = after[document_id]
        if old.version_id == new.version_id and old.blocks != new.blocks:
            raise ValueError(f"{location}: changed blocks reuse version_id for {document_id!r}")


def _evidence_keys(documents: tuple[Document, ...]) -> set[tuple[str, str, str]]:
    return {
        (document.document_id, document.version.version_id, block.block_key)
        for document in documents
        for block in document.version.blocks
    }


def _validate_evidence(
    value: object, keys: set[tuple[str, str, str]], location: str,
) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{location}: expected at least one evidence reference")
    for reference in value:
        fields = ("document_id", "version_id", "block_key")
        if not isinstance(reference, dict) or any(
            not isinstance(reference.get(field), str) for field in fields
        ):
            raise ValueError(f"{location}: evidence requires document_id, version_id and block_key")
        if tuple(reference[field] for field in fields) not in keys:
            raise ValueError(f"{location}: evidence points outside the expected snapshot")


def _validate_annotations(
    case_id: str, request: ReviewRequest, annotations: list[dict[str, object]],
) -> None:
    location = f"case {case_id!r}"
    artifact_ids = {item.item_id for item in request.artifact}
    if len(artifact_ids) != len(request.artifact):
        raise ValueError(f"{location}: duplicate artifact item_id")
    if not isinstance(annotations, list):
        raise ValueError(f"{location}: annotations must be an array")

    before = _evidence_keys(request.corpus.before)
    after = _evidence_keys(request.corpus.after)
    seen = set()
    for annotation in annotations:
        if not isinstance(annotation, dict):
            raise ValueError(f"{location}: annotation must be an object")
        item_id = annotation.get("item_id")
        if not isinstance(item_id, str) or item_id not in artifact_ids:
            raise ValueError(f"{location}: annotation has unknown item_id {item_id!r}")
        if item_id in seen:
            raise ValueError(f"{location}: duplicate annotation for {item_id!r}")
        seen.add(item_id)
        item_location = f"{location}, item {item_id!r}"
        judgment = annotation.get("judgment")
        if judgment not in ("material", "immaterial", "not_affected"):
            raise ValueError(f"{item_location}: invalid judgment {judgment!r}")

        has_provenance = "baseline_claim_ids" in annotation or "baseline_evidence" in annotation
        if has_provenance:
            claims = annotation.get("baseline_claim_ids")
            if not isinstance(claims, list) or not claims or any(
                not isinstance(claim, str) or not claim.strip() for claim in claims
            ):
                raise ValueError(f"{item_location}: baseline_claim_ids must contain claim IDs")
            _validate_evidence(annotation.get("baseline_evidence"), before, f"{item_location} baseline")
        if judgment == "material" and (has_provenance or "revised_text" in annotation or "evidence" in annotation):
            revision = annotation.get("revised_text")
            if not isinstance(revision, str) or not revision.strip():
                raise ValueError(f"{item_location}: material annotations require revised_text")
            _validate_evidence(annotation.get("evidence"), after, f"{item_location} revision")

    missing = artifact_ids - seen
    if missing:
        raise ValueError(f"{location}: missing annotation for {', '.join(sorted(missing))}")


def load_goldset(path: Path) -> GoldSet:
    """Decode all cases before evaluating any system."""
    source = path.read_bytes()
    data = json.loads(source)
    profile_version = data["profile_version"]
    if not isinstance(profile_version, str) or not profile_version.strip():
        raise ValueError("profile_version must be a non-empty string")

    cases = []
    seen_cases = set()
    for case in data["cases"]:
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_cases:
            raise ValueError(f"Invalid or duplicate case_id {case_id!r}")
        seen_cases.add(case_id)
        corpus = case["corpus"]
        request = ReviewRequest(
            corpus=Corpus(
                before=_snapshot(corpus["before"], f"case {case_id!r}, corpus.before"),
                after=_snapshot(corpus["after"], f"case {case_id!r}, corpus.after"),
            ),
            artifact=tuple(ArtifactItem(**item) for item in case["artifact"]),
            goal=case["goal"],
            budget=Budget(**case["budget"]),
            ledger=case.get("ledger"),
        )
        annotations = case["annotations"]
        _validate_revision(request.corpus, case_id)
        _validate_annotations(case_id, request, annotations)
        cases.append(GoldCase(case_id, request, tuple(annotations)))
    return GoldSet(profile_version, hashlib.sha256(source).hexdigest(), tuple(cases))
