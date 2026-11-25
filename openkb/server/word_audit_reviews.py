"""Turn retained document revisions into evidence-linked Word decision reviews."""

from dataclasses import asdict
from functools import cache
from uuid import uuid4

from openkb.core.delivery import Slot
from openkb.core.review import trace_blocks
from openkb.ingest.ledger import Ledger, StoreError
from openkb.ingest.model import Version
from openkb.ingest.sqlite import SqliteStore
from openkb.profiles.word_accessibility import audit_coverages


REVIEW_STATE_LABELS = {
    "pending": "待人工确认",
    "unchanged": "已复核",
    "confirmed": "已人工确认",
    "failed": "复核失败",
    "superseded": "已被新版复核取代",
}


def _effective_proof(item: dict, proof: dict, acknowledged: dict) -> tuple[str, str, dict, dict | None]:
    claim_id = item["claim_id"]
    check = item["check"]
    while (reviewed := acknowledged.get((item["slot_id"], proof["id"]))) is not None:
        if reviewed["new_evidence"] is None:
            return claim_id, check, proof, reviewed
        proof = reviewed["new_evidence"]
        check = reviewed["new_check"]
        claim_id = reviewed.get("new_claim_id") or claim_id
    return claim_id, check, proof, None


def _review_for_decision(
    after: Version, decision: dict, earlier_reviews: tuple[dict, ...], align, coverages,
) -> dict | None:
    review = {
        "id": uuid4().hex,
        "decision_id": decision["id"],
        "version_id": after.id,
        "state": "pending",
        "items": [],
    }
    acknowledged = {
        (item["slot_id"], item["old_evidence"]["id"]): item
        for prior in reversed(earlier_reviews)
        if prior["state"] in {"confirmed", "unchanged"}
        for item in prior["items"]
        if item["new_evidence"] is not None or item.get("basis") in {"missing", "conflict"}
    }
    try:
        for item in decision["audit"]["covered"]:
            for proof in item["evidence"]:
                if proof["document_id"] != after.document_id or proof["version_id"] == after.id:
                    continue
                claim_id, check, proof, observed = _effective_proof(item, proof, acknowledged)
                traces, _warnings = trace_blocks(align(proof["version_id"]), (proof["block_id"],))
                trace = traces[proof["block_id"]]
                alternatives = coverages(item["slot_id"])
                checks = {alternative.check for alternative in alternatives}
                if observed is None and trace.status == "unchanged" and len(checks) <= 1:
                    continue
                if len(checks) > 1:
                    basis = "conflict"
                elif trace.status == "ambiguous":
                    basis = "unresolved"
                elif not alternatives:
                    possible_rule = any(
                        "header" in candidate["text"].casefold() or "表头" in candidate["text"]
                        for candidate in trace.candidates
                    )
                    basis = "unresolved" if possible_rule else "missing"
                else:
                    basis = "unchanged" if observed is None and check in checks else "material"
                if observed is not None and observed["basis"] == basis:
                    previous_checks = {item["check"] for item in observed["new_alternatives"]}
                    if basis == "missing" or checks == previous_checks:
                        continue
                candidate = alternatives[0] if basis in {"unchanged", "material"} else None
                review["items"].append({
                    "slot_id": item["slot_id"],
                    "claim_id": claim_id,
                    "old_check": check,
                    "old_evidence": proof,
                    "new_claim_id": candidate.claim_id if candidate else None,
                    "new_check": candidate.check if candidate else None,
                    "new_evidence": asdict(candidate.evidence[0]) if candidate else None,
                    "new_alternatives": [asdict(value) for value in alternatives] if basis == "conflict" else [],
                    "basis": basis,
                    "change": trace.status,
                })
    except (OSError, StoreError, KeyError, ValueError):
        return {**review, "state": "failed"}
    if not review["items"]:
        return None
    if all(item["basis"] == "unchanged" for item in review["items"]):
        review["state"] = "unchanged"
    return review


def _revision_review(store: SqliteStore, after: Version):
    ledger = Ledger(store)
    align = cache(lambda version_id: ledger.align(version_id, after.id))

    @cache
    def coverages(slot_id: str):
        return audit_coverages(Slot(slot_id, ""), (after,), ledger, store, eligible=lambda *_: True)

    def calculate(decision: dict):
        prior = store.word_audit_reviews(decision["id"], before_version=after.id)
        return _review_for_decision(after, decision, prior, align, coverages)

    return calculate


def reviews_for_revision(store: SqliteStore, after: Version) -> list[dict]:
    calculate = _revision_review(store, after)
    return [
        review for decision in store.word_audit_import_decisions(after.id)
        if (review := calculate(decision)) is not None
    ]


def process_imports(store: SqliteStore) -> None:
    """Consume durable import events. Recording reviews and consuming an event are atomic."""
    for version_id in store.pending_word_audit_imports():
        after = store.version(version_id)
        reviews = reviews_for_revision(store, after)
        store.finish_word_audit_import(version_id, reviews)


def retry_failed_review(store: SqliteStore, review_id: str) -> None:
    review = store.word_audit_review(review_id)
    if review["state"] != "failed":
        raise ValueError("此复核已恢复。")
    after = store.version(review["version_id"])
    decision = store.word_audit_decision(review["decision_id"])
    refreshed = _revision_review(store, after)(decision)
    snapshot = refreshed if refreshed is not None else {**review, "state": "unchanged", "items": []}
    store.retry_word_audit_review(review_id, {**snapshot, "id": review_id})
