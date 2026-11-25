"""Shared checklist generation for the CLI and local browser."""

from collections.abc import Iterable
from dataclasses import asdict
from functools import partial
from uuid import uuid4

from openkb.core.delivery import MAX_STEPS, execute_delivery
from openkb.ingest.ledger import Ledger
from openkb.ingest.sqlite import SqliteStore
from openkb.profiles.word_accessibility import CHECKLIST_TITLE, SKELETON, find_coverage


def start_delivery(
    store: SqliteStore, message: str, sources: Iterable[str], *, max_steps: int = MAX_STEPS,
) -> dict:
    message = message.strip()
    if not message:
        raise ValueError("A delivery request must contain a message")
    source_ids = tuple(dict.fromkeys(sources))
    ledger = Ledger(store)
    versions = tuple(ledger.version(version_id) for version_id in source_ids)
    task_id = uuid4().hex

    def task_snapshot(result: dict) -> dict:
        artifact = None
        if result["state"] == "done":
            artifact = {
                "id": task_id,
                "kind": "checklist",
                "title": CHECKLIST_TITLE,
                "items": [
                    {**coverage, "title": slot.title}
                    for slot, coverage in zip(SKELETON, result["covered"], strict=True)
                ],
            }
        return {
            "id": task_id,
            "kind": "deliver",
            "message": message,
            "sources": source_ids,
            "skeleton": [asdict(slot) for slot in SKELETON],
            "artifact": artifact,
            "skeleton_proposals": [],
            **result,
        }

    result = execute_delivery(
        SKELETON,
        partial(find_coverage, sources=versions, ledger=ledger, claims=store),
        partial(store.open_delivery_conflicts, source_ids),
        lambda completed: store.save_delivery(task_snapshot(completed)),
        max_steps=max_steps,
    )
    data = task_snapshot(result)
    if result["state"] == "paused":
        store.save_delivery(data)
    return data
