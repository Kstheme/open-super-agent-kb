"""The Section 508 template for a non-web Word self-check."""

from dataclasses import dataclass

from openkb.core.delivery import Coverage, Slot
from openkb.ingest.claims import ClaimStore, compliance_claim
from openkb.ingest.ledger import EvidenceValidator, Ledger
from openkb.ingest.model import Block, Evidence, Version


SKELETON = (
    Slot("scope", "适用范围"),
    Slot("headings", "标题结构"),
    Slot("data_tables", "数据表格"),
    Slot("image_descriptions", "图片说明"),
)
CHECKLIST_TITLE = "非网页 Word 文档无障碍自查清单"
SCOPE_SOURCE = "508-applicability-checklist.docx"
WORD_SOURCE = "section508-word.docx"


@dataclass(frozen=True)
class Rule:
    source: str
    section: str | None
    quotes: tuple[str, ...]
    check: str
    method: str
    equivalent_claim: str | None = None


RULES = {
    "scope": Rule(
        SCOPE_SOURCE,
        None,
        ("2.4.1 Bypass Blocks (not required for non-web documents and software)",),
        "非网页 Word 文档不要求 2.4.1 Bypass Blocks。",
        "确认自查对象是非网页 Word 文档。",
        "2.4.1 Bypass Blocks is not required for non-web documents and software.",
    ),
    "headings": Rule(
        WORD_SOURCE,
        "Headings",
        (
            "The document uses MS Word heading styles.",
            'Headings in the "Navigation pane" match the document\'s visual outline.',
        ),
        "正文标题使用 Word 标题样式，层级符合文档结构。",
        "打开 Word 导航窗格，对照正文标题及其层级，确认两者大纲一致。"
        "再检查正文标题应用了对应的 Word 标题样式。",
    ),
    "data_tables": Rule(
        WORD_SOURCE,
        "Data Tables",
        ("Tables repeat their header row.",),
        "有数据表格时，表头行应设置为重复。",
        "在 Word 中选中数据表格的表头行，检查是否已设置为重复标题行。"
        "文档没有数据表格时记为不适用。",
    ),
    "image_descriptions": Rule(
        WORD_SOURCE,
        "Images and other Objects",
        ("Images/objects have Alt Text or described in context.",),
        "有图片或其他对象时，应提供替代文本或正文说明。",
        "逐个查看图片和其他对象，检查替代文本或正文中是否有相应说明。"
        "文档没有此类对象时记为不适用。",
    ),
}


def _quote_offset(block: Block, section: str | None, quote: str) -> int | None:
    if section is None:
        if block.kind == "paragraph" and block.text.lstrip("☐ ").strip() == quote:
            return block.text.index(quote)
        return None
    if block.kind != "table_row" or block.heading_path[-1:] != (section,):
        return None
    for cell in block.locator.table_cells:
        start, end = cell.text_start, cell.text_end
        if start is not None and end is not None and block.text[start:end] == quote:
            return start
    return None


def _source_evidence(version: Version, rule: Rule) -> tuple[Evidence, ...]:
    validator = EvidenceValidator(version)
    evidence = []
    for quote in rule.quotes:
        for block in version.blocks:
            offset = _quote_offset(block, rule.section, quote)
            if offset is not None:
                evidence.append(validator.validate(block.id, quote, quote_start=offset))
                break
        else:
            return ()
    return tuple(evidence)


def _same_basis(
    recorded: tuple[Evidence, ...], expected: tuple[Evidence, ...], version: Version, rule: Rule,
) -> bool:
    if set(recorded) == set(expected):
        return True
    # M1 extraction can include the applicability checklist's leading checkbox.
    if rule.section is not None or len(recorded) != 1:
        return False
    (actual,), (plain,) = recorded, expected
    if (
        actual.block_id != plain.block_id
        or actual.quote != "☐" + plain.quote
        or actual.quote_start != plain.quote_start - 1
        or plain.quote_start == 0
    ):
        return False
    return actual == EvidenceValidator(version).validate(
        actual.block_id, actual.quote, quote_start=actual.quote_start,
    )


def supports_slot(source: Version, slot_id: str) -> bool:
    return source.original_name.casefold() == RULES[slot_id].source


def find_coverage(
    slot: Slot,
    sources: tuple[Version, ...],
    ledger: Ledger,
    claims: ClaimStore,
) -> Coverage | None:
    rule = RULES[slot.id]
    for version in sources:
        if not supports_slot(version, slot.id):
            continue
        evidence = _source_evidence(version, rule)
        if not evidence:
            continue
        candidate = compliance_claim(version.id, rule.check, evidence)
        claim = next(
            (claim for claim in claims.claims(version.id)
             if claim.profile == candidate.profile
             and claim.text.strip() in (rule.check, rule.equivalent_claim)
             and _same_basis(claim.evidence, evidence, version, rule)),
            None,
        )
        if claim is None:
            claims.save_claims((candidate,))
            claim = candidate
        return Coverage(
            slot.id, claim.id, rule.check, rule.method,
            tuple(ledger.locate_evidence(proof.id) for proof in claim.evidence),
        )
    return None
