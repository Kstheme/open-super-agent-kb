import re
from io import BytesIO
from statistics import median
from typing import Callable

from markdown_it import MarkdownIt
from pdfplumber import open as open_pdf
from pdfminer.pdfexceptions import PDFException

from .docx import parse_docx
from .model import Locator, ParsedBlock, ParsedDocument


_ATX_ANCHOR = re.compile(r"\s+\{#[^}]+\}\s*$")
_PDF_CLAUSE = re.compile(r"^§\s*(\d+(?:\.\d+)+(?:[a-z]|-\d+)?)\s+\S")
_PDF_PARAGRAPH = re.compile(r"^\([0-9a-z]+\)\s")


def parse_markdown(data: bytes) -> ParsedDocument:
    source = data.decode("utf-8-sig")
    lines = source.splitlines(keepends=True)
    body = lines.copy()
    if lines and lines[0].strip() == "---":
        closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if closing is not None:
            body[:closing + 1] = ["\n"] * (closing + 1)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    headings = []
    blocks = []
    tokens = MarkdownIt("commonmark").enable("table").parse("".join(body))
    for index, token in enumerate(tokens):
        if token.map is None:
            continue
        start, end = token.map
        location = Locator(line_start=start + 1, line_end=end)
        if token.type == "heading_open":
            title = _ATX_ANCHOR.sub("", tokens[index + 1].content)
            level = int(token.tag[1:])
            headings = [(depth, text) for depth, text in headings if depth < level]
            headings.append((level, title))
            path = tuple(text for _, text in headings)
            blocks.append(ParsedBlock(
                title, path, kind="heading", locator=location,
                start_offset=offsets[start], end_offset=offsets[end],
            ))
        elif token.type in ("paragraph_open", "fence", "code_block", "tr_open"):
            if token.type == "tr_open" and lines[start].strip().replace("|", "").replace("-", "").strip() == "":
                continue
            text = "".join(lines[start:end]).rstrip()
            path = tuple(text for _, text in headings)
            kind = "paragraph" if token.type == "paragraph_open" else (
                "table_row" if token.type == "tr_open" else "code"
            )
            if text:
                blocks.append(ParsedBlock(
                    text, path, kind=kind, locator=location,
                    start_offset=offsets[start], end_offset=offsets[start] + len(text),
                ))
    return ParsedDocument(tuple(blocks))


def _page_regions(page):
    # Split two columns only when the middle has a clear gutter in a substantial body.
    center = (page.bbox[0] + page.bbox[2]) / 2
    body = [char for char in page.chars if char["text"].strip()
            and page.bbox[1] + page.height * .12 < char["top"] < page.bbox[1] + page.height * .9]
    left = sum(char["x1"] < center - 5 for char in body)
    right = sum(char["x0"] > center + 5 for char in body)
    if min(left, right) >= 50 and len(body) - left - right <= len(body) * .005:
        x0, top, x1, bottom = page.bbox
        return (page.crop((x0, top, center, bottom)), page.crop((center, top, x1, bottom)))
    return (page,)


def _bold(line: dict) -> bool:
    return all(
        any(weight in char["fontname"].casefold() for weight in ("bold", "demi"))
        for char in line["chars"] if char["text"].strip()
    )


def _joined(lines: list[dict]) -> str:
    text = lines[0]["text"]
    for line in lines[1:]:
        if text.endswith("-") and line["text"][0].islower():
            text = text[:-1] + line["text"]
        else:
            text += " " + line["text"]
    return text


def _pdf_paragraphs(lines: list[dict]) -> list[list[dict]]:
    groups = []
    interval = median(
        [right["top"] - left["top"] for left, right in zip(lines, lines[1:])
         if right["top"] > left["top"]] or [10]
    )
    for line in lines:
        if not groups or _PDF_PARAGRAPH.match(line["text"]) or (
            line["top"] - groups[-1][-1]["top"] > interval * 1.45
        ):
            groups.append([])
        groups[-1].append(line)
    return groups


def _pdf_sections(lines):
    index = 0
    preceding = []
    while index < len(lines):
        line = lines[index]
        clause = _PDF_CLAUSE.match(line["text"])
        if not clause or not _bold(line):
            preceding.append(line)
            index += 1
            continue
        for paragraph in _pdf_paragraphs(preceding):
            yield None, paragraph
        preceding = []
        heading = [line]
        index += 1
        while (heading[-1]["text"].endswith("-") and index < len(lines)
               and lines[index]["text"][0].islower() and _bold(lines[index])):
            heading.append(lines[index])
            index += 1
        yield clause.group(1), heading
    for paragraph in _pdf_paragraphs(preceding):
        yield None, paragraph


def parse_pdf(data: bytes) -> ParsedDocument:
    blocks = []
    active_heading = ()
    active_clause = None
    textless_pages = []
    try:
        with open_pdf(BytesIO(data)) as document:
            for page in document.pages:
                if not any(char["text"].strip() for char in page.chars):
                    textless_pages.append(page.page_number)
                    continue
                upright = page.filter(lambda item: item.get("upright", True))
                for region in _page_regions(upright):
                    lines = region.extract_text_lines()
                    if lines and re.fullmatch(r"§\s*\d+(?:\.\d+)+", lines[0]["text"]):
                        lines = lines[1:]
                    lines = [
                        line for line in lines
                        if not (
                            line["top"] > page.bbox[1] + page.height * .9
                            and re.match(r"^(?:\d+$|VerDate\s|Sfmt\s)", line["text"])
                        )
                    ]
                    for clause, segment in _pdf_sections(lines):
                        text = _joined(segment)
                        if clause is not None:
                            active_heading = (text,)
                            active_clause = clause
                        box = (
                            min(line["x0"] for line in segment), segment[0]["top"],
                            max(line["x1"] for line in segment), segment[-1]["bottom"],
                        )
                        blocks.append(ParsedBlock(
                            text, active_heading, active_clause,
                            "heading" if clause is not None else "paragraph",
                            Locator(page=page.page_number, bbox=box),
                        ))
                page.close()
    except (PDFException, ValueError) as error:
        raise ValueError("Cannot parse PDF") from error
    if not blocks:
        return ParsedDocument(
            (ParsedBlock("", kind="document"),),
            ("原文定位受限：PDF 没有可提取的文本，整份资料作为一个块。",),
        )
    warnings = ()
    if textless_pages:
        warnings = ("原文定位受限：以下 PDF 页没有可提取的文本：" + "、".join(map(str, textless_pages)),)
    return ParsedDocument(tuple(blocks), warnings)


PARSERS: dict[str, Callable[[bytes], ParsedDocument]] = {
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".pdf": parse_pdf,
    ".docx": parse_docx,
}
