"""Evidence-backed delivery of a non-web Word audit."""

from collections.abc import Iterable
from dataclasses import asdict
from functools import cache, partial
from uuid import uuid4

from openkb.core.delivery import MAX_STEPS, execute_delivery
from openkb.ingest.ledger import Ledger
from openkb.ingest.model import VersionSummary
from openkb.ingest.sqlite import SqliteStore
from openkb.profiles.word_accessibility import SKELETON, find_coverage, supports_audit_slot


AUDIT_SKELETON = tuple(slot for slot in SKELETON if slot.id in {"scope", "data_tables"})


def audit_sources(store: SqliteStore) -> tuple[VersionSummary, ...]:
    """List retained versions that can be selected for the audit template."""
    return tuple(
        version for version in store.list_versions()
        if any(supports_audit_slot(version, slot.id) for slot in AUDIT_SKELETON)
    )


def run_word_audit(
    store: SqliteStore, goal: str, sources: Iterable[str], *, max_steps: int = MAX_STEPS,
) -> dict:
    goal = goal.strip()
    if not goal:
        raise ValueError("请输入审阅目标。")

    source_ids = tuple(dict.fromkeys(sources))
    if not source_ids:
        latest = {}
        for version in audit_sources(store):
            latest.setdefault(version.document_id, version.id)
        source_ids = tuple(latest.values())

    ledger = Ledger(store)
    versions = tuple(ledger.version(version_id) for version_id in source_ids)
    task_id = uuid4().hex
    find = cache(partial(find_coverage, sources=versions, ledger=ledger, claims=store,
                         match_source=supports_audit_slot))
    result = execute_delivery(
        AUDIT_SKELETON, find, tuple, lambda _completed: (), max_steps=max_steps,
    )
    audit = {
        "id": task_id,
        "goal": goal,
        "sources": source_ids,
        "skeleton": [asdict(slot) for slot in AUDIT_SKELETON],
        **result,
    }
    store.save_word_audit(audit)
    return audit
