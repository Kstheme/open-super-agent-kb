"""Evidence-backed delivery of a non-web Word audit."""

from collections.abc import Iterable
from dataclasses import asdict
import re
from uuid import uuid4

from openkb.core.delivery import CoverageUnavailable, MAX_STEPS, execute_delivery
from openkb.ingest.ledger import Ledger
from openkb.ingest.model import Version, VersionSummary
from openkb.ingest.sqlite import SqliteStore
from openkb.profiles.word_accessibility import (
    MULTIPAGE_TABLE_CHECK, SKELETON, audit_coverages, supports_audit_slot,
)


AUDIT_SKELETON = tuple(slot for slot in SKELETON if slot.id in {"scope", "data_tables"})


def _source_roles(
    versions: tuple[VersionSummary, ...],
) -> set[tuple[str, str]]:
    return {
        (version.document_id, slot.id) for version in versions for slot in AUDIT_SKELETON
        if supports_audit_slot(version, slot.id)
    }


def audit_sources(store: SqliteStore) -> tuple[VersionSummary, ...]:
    """List retained versions belonging to a supported rule document."""
    versions = store.list_versions()
    roles = _source_roles(versions)
    return tuple(
        version for version in versions
        if any((version.document_id, slot.id) in roles for slot in AUDIT_SKELETON)
    )


def audit_source_versions(
    store: SqliteStore, sources: Iterable[str],
) -> tuple[tuple[Version, ...], set[tuple[str, str]], tuple[str, ...]]:
    retained = store.list_versions()
    roles = _source_roles(retained)
    by_id = {version.id: version for version in retained}
    selected = tuple(dict.fromkeys(sources))
    for version_id in selected:
        if version_id not in by_id:
            raise KeyError(version_id)

    eligible_documents = {document_id for document_id, _ in roles}
    by_document = {}
    notes = []
    for version in retained:
        if selected and version.id not in selected:
            continue
        if version.document_id not in eligible_documents:
            if selected:
                notes.append(f"{version.original_name} 不属于本次审阅支持的规则资料。")
            continue
        latest = by_document.setdefault(version.document_id, version)
        if selected and latest.id != version.id:
            notes.append(f"同一资料采用所选的最新版本：{latest.original_name} · {latest.id}。")
    versions = tuple(store.version(version.id) for version in by_document.values())
    return versions, roles, tuple(notes)


PAGE_SPANS = {
    "multipage": "表格跨页",
    "singlepage": "表格均在单页内",
    "mixed": "既有跨页表格，也有单页表格",
}


def _stated_page_span(goal: str) -> str:
    single = spans = False
    for clause in re.split(r"[，。；！？,.!?;]", goal):
        if any(word in clause for word in (
            "如果", "假设", "若", "是否", "会不会", "可能", "不确定", "没核实", "未核实", "待核对", "吗",
        )):
            continue
        single |= bool(re.search(
            r"(?:[一两三123]张\s*单页(?:数据)?表|(?:表格|数据表|这张表)(?:(?:不|未|没有)跨页|(?:均|都)?在单页内))",
            clause,
        ))
        spans |= bool(re.search(
            r"(?:[一两三123]张\s*跨页(?:数据)?表|(?:表格|数据表|这张表)(?:均|都|已经|确实|已)?跨页)",
            clause,
        ))
    if single and spans:
        return "mixed"
    return "multipage" if spans else "singlepage" if single else ""


def run_word_audit(
    store: SqliteStore, goal: str, sources: Iterable[str], *, max_steps: int | None = None,
    deliverable: dict | None = None, previous: dict | None = None, page_span: str = "",
) -> dict:
    goal = (previous["goal"] if previous is not None else goal).strip()
    if not goal:
        raise ValueError("请输入审阅目标。")

    if page_span and page_span not in PAGE_SPANS:
        raise ValueError("请选择表格是否跨页。")
    page_span = page_span or (previous.get("page_span", "") if previous else _stated_page_span(goal))
    if previous is not None and not previous["gaps"]:
        if previous.get("needs_page_span") and page_span:
            updated = {**previous, "state": "done", "needs_page_span": False, "page_span": page_span}
            store.save_word_audit(updated, previous=previous)
            return updated
        return previous

    versions, roles, notes = audit_source_versions(store, sources)
    ledger = Ledger(store)
    covered = {item["slot_id"]: item for item in previous["covered"]} if previous else {}
    pending = tuple(slot for slot in AUDIT_SKELETON if slot.id not in covered)
    evidence_conflicts = {
        conflict["slot_id"]: conflict
        for conflict in (previous.get("evidence_conflicts", ()) if previous else ())
    }

    def find(slot):
        try:
            alternatives = audit_coverages(
                slot, versions, ledger, store,
                eligible=lambda version, slot_id: (version.document_id, slot_id) in roles,
            )
        except OSError as error:
            raise CoverageUnavailable from error
        evidence_conflicts.pop(slot.id, None)
        if len({item.check for item in alternatives}) > 1:
            evidence_conflicts[slot.id] = {
                "slot_id": slot.id, "alternatives": [asdict(item) for item in alternatives],
            }
            return None
        return next(iter(alternatives), None)

    result = execute_delivery(
        pending, find, tuple, lambda _completed: (),
        max_steps=MAX_STEPS if max_steps is None else max_steps, retry_uncovered=False,
    )
    covered.update((item["slot_id"], item) for item in result["covered"])
    needs_page_span = (
        any(item["check"] == MULTIPAGE_TABLE_CHECK for item in covered.values()) and not page_span
    )
    if needs_page_span and result["state"] == "done":
        result["state"] = "waiting"
    prior_sources = previous["sources"] if previous else ()
    prior_notes = previous.get("source_notes", ()) if previous else ()
    audit = {
        **result,
        "id": previous["id"] if previous else uuid4().hex,
        "goal": goal,
        "sources": tuple(dict.fromkeys((*prior_sources, *(version.id for version in versions)))),
        "skeleton": [asdict(slot) for slot in AUDIT_SKELETON],
        "covered": [covered[slot.id] for slot in AUDIT_SKELETON if slot.id in covered],
        "evidence_conflicts": list(evidence_conflicts.values()),
        "source_notes": tuple(dict.fromkeys((*prior_notes, *notes))),
        "deliverable": previous.get("deliverable") if previous else deliverable,
        "page_span": page_span,
        "needs_page_span": needs_page_span,
    }
    store.save_word_audit(audit, previous=previous)
    return audit
