"""The Section 508 template for a non-web Word self-check."""

from openkb.core.delivery import Coverage, Slot
from openkb.ingest.claims import ClaimStore, compliance_claim
from openkb.ingest.ledger import EvidenceValidator, Ledger
from openkb.ingest.model import Version


SKELETON = (
    Slot("scope", "适用范围"),
    Slot("headings", "标题结构"),
    Slot("data_tables", "数据表格"),
    Slot("image_descriptions", "图片说明"),
)
SCOPE_SOURCE = "508-applicability-checklist.docx"
SCOPE_QUOTE = "2.4.1 Bypass Blocks (not required for non-web documents and software)"
SCOPE_CHECK = "非网页 Word 文档不要求 2.4.1 Bypass Blocks。"
SCOPE_METHOD = "确认自查对象是非网页 Word 文档。"


def find_coverage(
    slot: Slot,
    sources: tuple[Version, ...],
    ledger: Ledger,
    claims: ClaimStore,
) -> Coverage | None:
    if slot.id != "scope":
        return None
    for version in sources:
        if version.original_name.casefold() != SCOPE_SOURCE:
            continue
        for block in version.blocks:
            if block.text.lstrip("☐ ").strip() != SCOPE_QUOTE:
                continue
            evidence = EvidenceValidator(version).validate(block.id, SCOPE_QUOTE)
            claim = compliance_claim(version.id, SCOPE_CHECK, (evidence,))
            if claim not in claims.claims(version.id):
                claims.save_claims((claim,))
            return Coverage(
                slot.id, claim.id, SCOPE_CHECK, SCOPE_METHOD,
                (ledger.locate_evidence(evidence.id),),
            )
    return None
