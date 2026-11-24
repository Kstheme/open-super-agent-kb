from dataclasses import dataclass


@dataclass(frozen=True)
class TableCell:
    column: int
    column_span: int
    vertical_start_row: int | None = None
    text_start: int | None = None
    text_end: int | None = None


@dataclass(frozen=True)
class Locator:
    """A location in the retained original using one-based page, line and row indices."""

    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    line_start: int | None = None
    line_end: int | None = None
    paragraph: int | None = None
    table: int | None = None
    row: int | None = None
    table_cells: tuple[TableCell, ...] = ()


@dataclass(frozen=True)
class ParsedBlock:
    text: str
    heading_path: tuple[str, ...] = ()
    clause: str | None = None
    kind: str = "paragraph"
    locator: Locator = Locator()
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True)
class ParsedDocument:
    blocks: tuple[ParsedBlock, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Block(ParsedBlock):
    id: str = ""
    ordinal: int = 0
    block_key: str = ""
    norm_hash: str = ""


@dataclass(frozen=True)
class Version:
    id: str
    document_id: str
    original_name: str
    content_hash: str
    normalization_version: str
    blocks: tuple[Block, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evidence:
    id: str
    version_id: str
    block_id: str
    quote: str
    quote_start: int
    quote_end: int
    quote_hash: str


@dataclass(frozen=True)
class LocatedEvidence(Evidence):
    document_id: str
    original_name: str
    content_hash: str
    locator: Locator
