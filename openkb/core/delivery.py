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


def execute_delivery(
    skeleton: tuple[Slot, ...],
    find_coverage: Callable[[Slot], Coverage | None],
    *,
    max_steps: int = MAX_STEPS,
) -> dict:
    if not 0 <= max_steps <= MAX_STEPS:
        raise ValueError(f"Step budget must be between 0 and {MAX_STEPS}")

    pending = deque(skeleton)
    covered = {}
    steps = 0
    started = monotonic()
    pause_reason = None
    while pending:
        if monotonic() - started >= MAX_WALL_SECONDS:
            pause_reason = "wall clock budget exhausted"
            break
        if steps >= max_steps:
            pause_reason = "step budget exhausted"
            break
        slot = pending.popleft()
        steps += 1
        coverage = find_coverage(slot)
        if coverage is None:
            pending.append(slot)
        else:
            if coverage.slot_id != slot.id:
                raise ValueError("Coverage does not match the requested slot")
            covered[slot.id] = coverage

    gaps = [asdict(slot) for slot in skeleton if slot.id not in covered]
    return {
        "state": "paused" if gaps else "done",
        "pause_reason": pause_reason,
        "covered": [asdict(covered[slot.id]) for slot in skeleton if slot.id in covered],
        "gaps": gaps,
        "budget": {
            "steps": steps,
            "max_steps": max_steps,
            "tokens": 0,
            "max_tokens": MAX_TOKENS,
            "max_wall_seconds": MAX_WALL_SECONDS,
        },
    }
