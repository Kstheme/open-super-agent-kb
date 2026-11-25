"""Evidence-backed delivery of a non-web Word audit."""

from collections.abc import Iterable
from dataclasses import asdict
from functools import cache
from uuid import uuid4

from openkb.core.delivery import MAX_STEPS, execute_delivery
from openkb.ingest.ledger import Ledger
from openkb.ingest.model import Version, VersionSummary
from openkb.ingest.sqlite import SqliteStore
from openkb.profiles.word_accessibility import SKELETON, audit_coverages, supports_audit_slot


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


def run_word_audit(
    store: SqliteStore, goal: str, sources: Iterable[str], *, max_steps: int = MAX_STEPS,
    deliverable: dict | None = None,
) -> dict:
    goal = goal.strip()
    if not goal:
        raise ValueError("请输入审阅目标。")

    versions, roles, notes = audit_source_versions(store, sources)
    ledger = Ledger(store)
    task_id = uuid4().hex
    evidence_conflicts = {}

    @cache
    def find(slot):
        alternatives = audit_coverages(
            slot, versions, ledger, store,
            eligible=lambda version, slot_id: (version.document_id, slot_id) in roles,
        )
        if len({item.check for item in alternatives}) > 1:
            evidence_conflicts[slot.id] = {
                "slot_id": slot.id, "alternatives": [asdict(item) for item in alternatives],
            }
            return None
        return next(iter(alternatives), None)

    result = execute_delivery(
        AUDIT_SKELETON, find, tuple, lambda _completed: (), max_steps=max_steps,
    )
    audit = {
        "id": task_id,
        "goal": goal,
        "sources": tuple(version.id for version in versions),
        "skeleton": [asdict(slot) for slot in AUDIT_SKELETON],
        "evidence_conflicts": list(evidence_conflicts.values()),
        "source_notes": notes,
        "deliverable": deliverable,
        **result,
    }
    store.save_word_audit(audit)
    return audit
