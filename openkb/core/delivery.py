"""Run a fixed-skeleton delivery and compute which slots lack coverage."""

from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from time import monotonic

from openkb.ingest.model import LocatedEvidence


MAX_STEPS = 40
MAX_TOKENS = 150_000
MAX_WALL_SECONDS = 600


@dataclass(frozen=True)
class Slot:
    id: str
    title: str


@dataclass(frozen=True)
class Coverage:
    slot_id: str
    claim_id: str
    check: str
    method: str
    evidence: tuple[LocatedEvidence, ...]


@dataclass(frozen=True)
class DeliveryConflict:
    id: str
    slot_id: str
    source_version: str
    reason: str
    state: str = "open"


def execute_delivery(
    skeleton: tuple[Slot, ...],
    find_coverage: Callable[[Slot], Coverage | None],
    find_conflicts: Callable[[], tuple[DeliveryConflict, ...]],
    save_completed: Callable[[dict], tuple[DeliveryConflict, ...]],
    *,
    max_steps: int = MAX_STEPS,
) -> dict:
    if not 0 <= max_steps <= MAX_STEPS:
        raise ValueError(f"Step budget must be between 0 and {MAX_STEPS}")

    pending = deque(skeleton)
    covered: dict[str, Coverage] = {}
    steps = 0
    started = monotonic()
    conflicts = find_conflicts()

    def remove_conflicted_slots() -> None:
        blocked = {conflict.slot_id for conflict in conflicts}
        for slot in skeleton:
            if slot.id in blocked and covered.pop(slot.id, None) is not None:
                pending.append(slot)

    def snapshot(state: str, pause_reason: str | None = None) -> dict:
        return {
            "state": state,
            "pause_reason": pause_reason,
            "covered": [asdict(covered[slot.id]) for slot in skeleton if slot.id in covered],
            "gaps": [asdict(slot) for slot in skeleton if slot.id not in covered],
            "conflicts": [asdict(conflict) for conflict in conflicts],
            "budget": {
                "steps": steps,
                "max_steps": max_steps,
                "tokens": 0,
                "max_tokens": MAX_TOKENS,
                "max_wall_seconds": MAX_WALL_SECONDS,
            },
        }

    while True:
        if not pending:
            result = snapshot("done")
            conflicts = save_completed(result)
            if not conflicts:
                return result
            # Saving completion rechecks the ledger under its write lock.
            remove_conflicted_slots()

        if monotonic() - started >= MAX_WALL_SECONDS:
            return snapshot("paused", "wall clock budget exhausted")
        if steps >= max_steps:
            return snapshot("paused", "step budget exhausted")
        slot = pending.popleft()
        steps += 1
        coverage = find_coverage(slot)
        if coverage is None:
            pending.append(slot)
        else:
            if coverage.slot_id != slot.id:
                raise ValueError("Coverage does not match the requested slot")
            covered[slot.id] = coverage

        conflicts = find_conflicts()
        remove_conflicted_slots()
