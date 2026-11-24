import hashlib
import json
import unicodedata
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from .model import Block, Evidence, LocatedEvidence, Version
from .parsers import PARSERS


NORMALIZATION_VERSION = "1"


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


class Ledger:
    def __init__(self, store: Store) -> None:
        self.store = store

    def add(self, path: Path, document_id: str | None = None) -> Version:
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
            return self.version(version_id)
        except KeyError:
            pass
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
        return self.store.save(version, raw)

    def version(self, version_id: str) -> Version:
        return self.store.version(version_id)

    def original(self, version_id: str) -> bytes:
        return self.store.original(version_id)

    def validate_evidence(
        self, version_id: str, block_id: str, quote: str, *, quote_start: int | None = None,
    ) -> Evidence:
        version = self.version(version_id)
        block = self._evidence_block(version, block_id)
        if not quote or not quote.strip():
            raise ValueError("Evidence quote must contain text")
        if quote_start is None:
            quote_start = block.text.find(quote)
            if quote_start >= 0 and block.text.find(quote, quote_start + 1) >= 0:
                raise ValueError("Evidence quote is ambiguous; specify its start offset")
        if quote_start < 0 or not block.text.startswith(quote, quote_start):
            raise ValueError("Evidence quote does not match the selected block and offset")
        quote_end = quote_start + len(quote)
        quote_hash = _hash(quote.encode("utf-8"))
        return Evidence(
            _key(version_id, block_id, str(quote_start), str(quote_end), quote_hash),
            version_id, block_id, quote, quote_start, quote_end, quote_hash,
        )

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
        for block in version.blocks:
            if block.id == block_id:
                if block.kind == "document" and not block.text:
                    raise ValueError("Evidence requires a locatable text block")
                return block
        raise ValueError("Block does not belong to the selected version")

    def align(self, before_id: str, after_id: str) -> dict:
        before, after = self.version(before_id), self.version(after_id)
        if before.document_id != after.document_id:
            raise ValueError("Cannot align versions of different documents")
        if before.normalization_version != after.normalization_version:
            raise ValueError("Cannot align different normalization versions")

        old = defaultdict(list)
        new = defaultdict(list)
        for block in before.blocks:
            old[block.block_key].append(block)
        for block in after.blocks:
            new[block.block_key].append(block)

        matches, ambiguous, added, removed = [], [], [], []
        for key in dict.fromkeys((*old, *new)):
            previous, current = old.get(key, ()), new.get(key, ())
            if not previous:
                added.extend(current)
            elif not current:
                removed.extend(previous)
            elif len(previous) == len(current) == 1:
                matches.append({"before": asdict(previous[0]), "after": asdict(current[0])})
            else:
                ambiguous.append({
                    "block_key": key,
                    "before": [asdict(block) for block in previous],
                    "after": [asdict(block) for block in current],
                })
        return {
            "normalization_version": before.normalization_version,
            "before_version": before_id,
            "after_version": after_id,
            "matches": matches,
            "ambiguous": ambiguous,
            "added": [asdict(block) for block in added],
            "removed": [asdict(block) for block in removed],
            "warnings": {"before": before.warnings, "after": after.warnings},
        }
