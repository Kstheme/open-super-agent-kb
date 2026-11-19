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


def load_goldset(path: Path) -> GoldSet:
    """Decode all cases before evaluating any system."""
    source = path.read_bytes()
    data = json.loads(source)
    profile_version = data["profile_version"]
    if not isinstance(profile_version, str) or not profile_version.strip():
        raise ValueError("profile_version must be a non-empty string")

    cases = []
    for case in data["cases"]:
        case_id = case["case_id"]
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
        cases.append(GoldCase(case_id, request, tuple(case["annotations"])))
    return GoldSet(profile_version, hashlib.sha256(source).hexdigest(), tuple(cases))
