"""Turn retained document revisions into evidence-linked Word decision reviews."""

from dataclasses import asdict
from functools import cache
from uuid import uuid4

from openkb.core.review import BlockTrace, trace_blocks
from openkb.ingest.ledger import EvidenceValidator, Ledger
from openkb.ingest.model import Version
from openkb.ingest.sqlite import SqliteStore


NARROWED_TABLE_RULE = "Only data tables that span multiple pages must repeat their header row."


def _replacement(
    ledger: Ledger, after: Version, slot_id: str, trace: BlockTrace,
) -> tuple[str, dict] | None:
    if slot_id != "data_tables" or trace.status != "changed":
        return None
    for block in trace.candidates:
        if block["heading_path"][-1:] != ("Data Tables",) or block["text"].count(NARROWED_TABLE_RULE) != 1:
            continue
        evidence = EvidenceValidator(after).validate(
            block["id"], NARROWED_TABLE_RULE, quote_start=block["text"].index(NARROWED_TABLE_RULE),
        )
        ledger.store.save_evidence(evidence)
        return (
            "只有跨页数据表格需要设置重复表头，单页数据表格不再适用。",
            asdict(ledger.locate_evidence(evidence.id)),
        )
    return None


def reviews_for_revision(store: SqliteStore, after: Version) -> list[dict]:
    ledger = Ledger(store)
    align = cache(lambda version_id: ledger.align(version_id, after.id))
    reviews = []
    for decision in store.word_audit_import_decisions(after.id):
        items = []
        for item in decision["audit"]["covered"]:
            for proof in item["evidence"]:
                if proof["document_id"] != after.document_id or proof["version_id"] == after.id:
                    continue
                traces, _warnings = trace_blocks(align(proof["version_id"]), (proof["block_id"],))
                trace = traces[proof["block_id"]]
                if trace.status == "unchanged":
                    continue
                replacement = _replacement(ledger, after, item["slot_id"], trace)
                items.append({
                    "slot_id": item["slot_id"],
                    "claim_id": item["claim_id"],
                    "old_check": item["check"],
                    "old_evidence": proof,
                    "new_check": replacement[0] if replacement else None,
                    "new_evidence": replacement[1] if replacement else None,
                    "change": trace.status,
                })
        if items:
            reviews.append({
                "id": uuid4().hex,
                "decision_id": decision["id"],
                "version_id": after.id,
                "state": "pending",
                "items": items,
            })
    return reviews


def process_imports(store: SqliteStore) -> None:
    """Consume durable import events. Recording reviews and consuming an event are atomic."""
    for version_id in store.pending_word_audit_imports():
        after = store.version(version_id)
        reviews = reviews_for_revision(store, after)
        store.finish_word_audit_import(version_id, reviews)
