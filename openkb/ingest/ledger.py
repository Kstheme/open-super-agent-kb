import hashlib
import json
import unicodedata
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from openkb.core.review import align_blocks

from .model import Block, Evidence, LocatedEvidence, Version
from .parsers import PARSERS


NORMALIZATION_VERSION = "1"


class StoreError(RuntimeError):
    """Storage failed while carrying out a ledger operation."""


class Store(Protocol):
    def save(self, version: Version, original: bytes) -> Version: ...
    def version(self, version_id: str) -> Version: ...
    def original(self, version_id: str) -> bytes: ...
    def save_evidence(self, evidence: Evidence) -> Evidence: ...
    def evidence(self, evidence_id: str) -> Evidence: ...


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _key(*values: str | tuple[str, ...]) -> str:
    return _hash(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _require_locatable_block(block: Block | None) -> Block:
    if block is None:
        raise ValueError("Block does not belong to the selected version")
    if block.kind == "document" and not block.text:
        raise ValueError("Evidence requires a locatable text block")
    return block


def _validate_quote(version_id: str, block: Block, quote: str, quote_start: int | None) -> Evidence:
    if not quote or not quote.strip():
        raise ValueError("Evidence quote must contain text")
    if quote_start is not None and type(quote_start) is not int:
        raise ValueError("Evidence quote offset must be an integer")
    if quote_start is None:
        quote_start = block.text.find(quote)
        if quote_start >= 0 and block.text.find(quote, quote_start + 1) >= 0:
            raise ValueError("Evidence quote is ambiguous; specify its start offset")
    if quote_start < 0 or not block.text.startswith(quote, quote_start):
        raise ValueError("Evidence quote does not match the selected block and offset")
    quote_end = quote_start + len(quote)
    if block.kind == "table_row":
        cells = block.locator.table_cells
        if cells and all(cell.text_start is not None and cell.text_end is not None for cell in cells):
            if not any(cell.text_start <= quote_start <= quote_end <= cell.text_end for cell in cells):
                raise ValueError("Evidence must point inside a single source table cell")
        elif " | " in quote:
            raise ValueError("Evidence must point inside a single source table cell")
    quote_hash = _hash(quote.encode("utf-8"))
    return Evidence(
        _key(version_id, block.id, str(quote_start), str(quote_end), quote_hash),
        version_id, block.id, quote, quote_start, quote_end, quote_hash,
    )


class EvidenceValidator:
    """Validate multiple quotes against one immutable document version."""

    def __init__(self, version: Version) -> None:
        self._version_id = version.id
        self._blocks = {block.id: block for block in version.blocks}

    def validate(self, block_id: str, quote: str, *, quote_start: int | None = None) -> Evidence:
        block = _require_locatable_block(self._blocks.get(block_id))
        return _validate_quote(self._version_id, block, quote, quote_start)


class Ledger:
    def __init__(self, store: Store) -> None:
        self.store = store

    def add(
        self, path: Path, document_id: str | None = None, *,
        save: Callable[[Version, bytes], Version] | None = None,
    ) -> Version:
        suffix = path.suffix.lower()
        if suffix not in PARSERS:
            raise ValueError(f"Unsupported document format: {suffix or path.name}")
        document = document_id.strip() if document_id is not None else path.resolve().as_uri()
        if not document:
            raise ValueError("Document identity must not be empty")
        raw = path.read_bytes()
        content_hash = _hash(raw)
        version_id = _key(document, content_hash, NORMALIZATION_VERSION)
        try:
            version = self.version(version_id)
        except KeyError:
            pass
        else:
            return save(version, raw) if save is not None else version
        parsed = PARSERS[suffix](raw)
        blocks = []
        for ordinal, source in enumerate(parsed.blocks, 1):
            headings = tuple(_normalize(title) for title in source.heading_path)
            normalized = (unicodedata.normalize("NFC", source.text) if source.kind == "code"
                          else _normalize(source.text))
            norm_hash = _hash(normalized.encode("utf-8")) if normalized else content_hash
            blocks.append(Block(
                source.text, source.heading_path, source.clause, source.kind, source.locator,
                source.start_offset, source.end_offset, _key(version_id, str(ordinal)), ordinal,
                _key(NORMALIZATION_VERSION, headings, _normalize(source.clause or ""), norm_hash),
                norm_hash,
            ))
        version = Version(
            version_id, document, path.name, content_hash, NORMALIZATION_VERSION,
            tuple(blocks), parsed.warnings,
        )
        return (save if save is not None else self.store.save)(version, raw)

    def version(self, version_id: str) -> Version:
        return self.store.version(version_id)

    def original(self, version_id: str) -> bytes:
        return self.store.original(version_id)

    def validate_evidence(
        self, version_id: str, block_id: str, quote: str, *, quote_start: int | None = None,
    ) -> Evidence:
        version = self.version(version_id)
        block = self._evidence_block(version, block_id)
        return _validate_quote(version.id, block, quote, quote_start)

    def add_evidence(
        self, version_id: str, block_id: str, quote: str, *, quote_start: int | None = None,
    ) -> Evidence:
        evidence = self.validate_evidence(version_id, block_id, quote, quote_start=quote_start)
        return self.store.save_evidence(evidence)

    def locate_evidence(self, evidence_id: str) -> LocatedEvidence:
        evidence = self.store.evidence(evidence_id)
        version = self.version(evidence.version_id)
        block = self._evidence_block(version, evidence.block_id)
        return LocatedEvidence(
            **asdict(evidence), document_id=version.document_id, original_name=version.original_name,
            content_hash=version.content_hash, locator=block.locator,
        )

    @staticmethod
    def _evidence_block(version: Version, block_id: str) -> Block:
        return _require_locatable_block(next((block for block in version.blocks if block.id == block_id), None))

    def align(self, before_id: str, after_id: str) -> dict:
        before, after = self.version(before_id), self.version(after_id)
        if before.document_id != after.document_id:
            raise ValueError("Cannot align versions of different documents")
        if before.normalization_version != after.normalization_version:
            raise ValueError("Cannot align different normalization versions")

        return {
            "normalization_version": before.normalization_version,
            "before_version": before_id,
            "after_version": after_id,
            **align_blocks([asdict(block) for block in before.blocks], [asdict(block) for block in after.blocks]),
            "warnings": {"before": before.warnings, "after": after.warnings},
        }
