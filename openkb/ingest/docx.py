"""Structure and version-local locations from a WordprocessingML document."""

import re
from collections.abc import Iterator
from io import BytesIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from .model import Locator, ParsedBlock, ParsedDocument, TableCell


_WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}
_CLAUSE = re.compile(r"^(§\s*\d+(?:\.\d+)*[a-z]?|第[〇零一二三四五六七八九十百千0-9]+条|\d+(?:\.\d+)+)(?=\s|[、：:]|$)")


class _WordDocument:
    def __init__(self, document: ElementTree.Element, styles: ElementTree.Element | None):
        namespace, _, tag = document.tag[1:].partition("}")
        if tag != "document" or namespace not in _WORD_NAMESPACES:
            raise ValueError("Expected a WordprocessingML document")
        self._ns = {"w": namespace}
        self._prefix = f"{{{namespace}}}"
        self.body = self._find(document, "w:body")
        if self.body is None:
            raise ValueError("Word document has no body")
        self.styles = (
            {style.get(self._name("styleId")): style
             for style in styles.findall("w:style", self._ns)}
            if styles is not None else {}
        )

    def _name(self, name: str) -> str:
        return self._prefix + name

    def _find(self, node: ElementTree.Element, path: str) -> ElementTree.Element | None:
        return node.find(path, self._ns)

    def _value(self, node: ElementTree.Element, path: str) -> str | None:
        element = self._find(node, path)
        return element.get(self._name("val")) if element is not None else None

    def _text(self, paragraph: ElementTree.Element) -> str:
        pieces = []
        for node in paragraph.iter():
            tag = node.tag.removeprefix(self._prefix)
            if tag == "t":
                pieces.append(node.text or "")
            elif tag == "tab":
                pieces.append("\t")
            elif tag in ("br", "cr"):
                pieces.append("\n")
            elif tag == "noBreakHyphen":
                pieces.append("‑")
        return "".join(pieces).strip()

    def _heading_depth(self, paragraph: ElementTree.Element) -> int | None:
        style_id = self._value(paragraph, "w:pPr/w:pStyle")
        level = self._value(paragraph, "w:pPr/w:outlineLvl")
        visited = set()
        while level is None and style_id is not None and style_id not in visited:
            visited.add(style_id)
            style = self.styles.get(style_id)
            if style is not None:
                level = self._value(style, "w:pPr/w:outlineLvl")
                if level is not None:
                    break
            builtin = style_id.casefold()
            if builtin == "title":
                return -1
            heading = re.fullmatch(r"heading([1-9])", builtin)
            if heading:
                return int(heading.group(1)) - 1
            style_id = self._value(style, "w:basedOn") if style is not None else None
        return int(level) if level is not None and level in "012345678" and len(level) == 1 else None

    def _rows(
        self, table: ElementTree.Element, index: int, headings: tuple[str, ...],
        clause: str | None,
    ) -> Iterator[ParsedBlock]:
        active_merges = {}
        for row_index, row in enumerate(table.findall("w:tr", self._ns), 1):
            column = 1 + int(self._value(row, "w:trPr/w:gridBefore") or "0")
            next_merges = {}
            cells = []
            text_parts = []
            offset = 0
            for cell in row.findall("w:tc", self._ns):
                span = int(self._value(cell, "w:tcPr/w:gridSpan") or "1")
                if span < 1:
                    raise ValueError("Word table column span must be positive")
                merge = self._find(cell, "w:tcPr/w:vMerge")
                origin = None
                if merge is not None:
                    origin = (row_index if merge.get(self._name("val")) == "restart"
                              else active_merges.get((column, span)))
                    if origin is not None:
                        next_merges[column, span] = origin
                text = (
                    "" if origin is not None and origin < row_index else
                    "\n".join(self._text(p) for p in cell.findall("w:p", self._ns)).strip()
                )
                cells.append(TableCell(column, span, origin, offset, offset + len(text)))
                text_parts.append(text)
                offset += len(text) + len(" | ")
                column += span
            if any(text_parts):
                yield ParsedBlock(
                    " | ".join(text_parts), headings, clause, "table_row",
                    Locator(table=index, row=row_index, table_cells=tuple(cells)),
                )
            active_merges = next_merges

    def blocks(self) -> Iterator[ParsedBlock]:
        headings = []
        paragraph = table = 0
        for node in self.body:
            if node.tag == self._name("p"):
                paragraph += 1
                text = self._text(node)
                if not text:
                    continue
                depth = self._heading_depth(node)
                match = _CLAUSE.match(text)
                explicit_clause = match.group(1) if match else None
                if depth is not None:
                    headings = [entry for entry in headings if entry[0] < depth]
                    headings.append((depth, text, explicit_clause))
                path = tuple(title for _, title, _ in headings)
                clause = explicit_clause or next(
                    (clause for _, _, clause in reversed(headings) if clause), None,
                )
                yield ParsedBlock(
                    text, path, clause, "heading" if depth is not None else "paragraph",
                    Locator(paragraph=paragraph),
                )
            elif node.tag == self._name("tbl"):
                table += 1
                path = tuple(title for _, title, _ in headings)
                clause = next((clause for _, _, clause in reversed(headings) if clause), None)
                yield from self._rows(node, table, path, clause)


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        with ZipFile(BytesIO(data)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
            styles = None
            if "word/styles.xml" in archive.namelist():
                styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        blocks = tuple(_WordDocument(root, styles).blocks())
    except (BadZipFile, KeyError, ElementTree.ParseError, ValueError) as error:
        raise ValueError(f"Cannot parse DOCX: {error}") from error
    if blocks:
        return ParsedDocument(blocks)
    return ParsedDocument(
        (ParsedBlock("", kind="document"),),
        ("原文定位受限：DOCX 正文没有可提取的文本。",),
    )
