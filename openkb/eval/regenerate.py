"""The M0 full-regeneration comparison system."""

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from time import perf_counter

from openkb.agent.model import BudgetSession, Generation, ModelClient

from .contract import (
    DEFAULT_ARTIFACT_SECTION, ArtifactItem, Cost, EvidenceReference, Proposal,
    ReviewRequest, ReviewResult, Timing,
)


REGENERATE_NOTE = "小语料可整读：regenerate 读取完整变更后资料，无需检索，这使对手基线处于有利条件。"
_JUDGMENTS = ["material", "immaterial", "not_affected"]
_EVIDENCE_KEYS = ("document_id", "version_id", "block_key")
_EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string", "minLength": 1} for key in _EVIDENCE_KEYS},
    "required": list(_EVIDENCE_KEYS),
    "additionalProperties": False,
}
_REGENERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string", "minLength": 1},
                    "section": {"type": "string", "minLength": 1},
                    "ordinal": {"type": "integer", "minimum": 1},
                    "text": {"type": "string", "minLength": 1},
                    "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA},
                },
                "required": ["section", "ordinal", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class _Slot:
    item_id: str
    section: str
    ordinal: int


def _layout(artifact: tuple[ArtifactItem, ...]) -> tuple[_Slot, ...]:
    occupied = {(item.section or DEFAULT_ARTIFACT_SECTION, item.ordinal) for item in artifact}
    next_ordinal = defaultdict(lambda: 1)
    slots = []
    for item in artifact:
        section = item.section or DEFAULT_ARTIFACT_SECTION
        ordinal = item.ordinal
        if ordinal is None:
            ordinal = next_ordinal[section]
            while (section, ordinal) in occupied:
                ordinal += 1
            next_ordinal[section] = ordinal + 1
        slots.append(_Slot(item.item_id, section, ordinal))
    return tuple(slots)


def _align(slots: tuple[_Slot, ...], regenerated: list[dict]) -> dict[str, dict]:
    """Match all stable IDs first, then fill unmatched slots one to one."""
    by_id = {}
    by_position = {}
    for index, item in enumerate(regenerated):
        item_id = item.get("item_id")
        position = item["section"], item["ordinal"]
        if item_id in by_id or position in by_position:
            raise ValueError("Regenerated items have a duplicate ID or section position")
        if item_id is not None:
            by_id[item_id] = index
        by_position[position] = index

    matches = {slot.item_id: by_id[slot.item_id] for slot in slots if slot.item_id in by_id}
    consumed = set(matches.values())
    for slot in slots:
        position_index = by_position.get((slot.section, slot.ordinal))
        if slot.item_id not in matches and position_index is not None and position_index not in consumed:
            matches[slot.item_id] = position_index
            consumed.add(position_index)
    return {item_id: regenerated[index] for item_id, index in matches.items()}


def _regenerate(request: ReviewRequest, session: BudgetSession) -> Generation:
    slots = _layout(request.artifact)
    if not slots:
        return Generation("done", value=())

    gaps = tuple(slot.item_id for slot in slots)
    regen_input = {
        "goal": request.goal,
        "outline": [asdict(slot) for slot in slots],
        "documents": [asdict(document) for document in request.corpus.after],
    }
    regenerated = session.generate(
        "请根据变更后的全部资料和目标，按给定的章节结构完整重新生成检查清单。"
        "每节的 ordinal 从 1 开始。能确认与骨架槽位对应时沿用 item_id，否则省略。"
        "如能定位证据，请引用这些资料中的 document_id、version_id 和 block_key。"
        "\n" + json.dumps(regen_input, ensure_ascii=False),
        _REGENERATION_SCHEMA, gaps=gaps,
    )
    if regenerated.state != "done":
        return regenerated

    try:
        aligned = _align(slots, regenerated.value["items"])
    except ValueError as error:
        return Generation("failed", reason=str(error), gaps=gaps)

    pairs = [{
        "item_id": item.item_id,
        "baseline": item.snapshot_text,
        "regenerated": aligned[item.item_id]["text"] if item.item_id in aligned else None,
    } for item in request.artifact]
    judgments = session.generate(
        "逐条比较基线清单和重新生成的清单。判定对义务、权限、范围或真值有实质变化的为 material，"
        "仅措辞变化为 immaterial，文本与含义均无变化为 not_affected。"
        "regenerated 为 null 表示新清单遗漏该条。按 item_id 给每个基线条目判定。"
        "\n" + json.dumps(pairs, ensure_ascii=False),
        {
            "type": "object",
            "properties": {item_id: {"enum": _JUDGMENTS} for item_id in gaps},
            "required": list(gaps),
            "additionalProperties": False,
        },
        gaps=gaps,
    )
    if judgments.state != "done":
        return judgments

    valid_evidence = {
        EvidenceReference(document.document_id, document.version.version_id, block.block_key)
        for document in request.corpus.after
        for block in document.version.blocks
    }
    proposals = []
    for item in request.artifact:
        judgment = judgments.value[item.item_id]
        if judgment == "not_affected":
            continue
        candidate = aligned.get(item.item_id, {})
        evidence = tuple(EvidenceReference(**ref) for ref in candidate.get("evidence", ()))
        if any(ref not in valid_evidence for ref in evidence):
            return Generation("failed", reason="Regenerated evidence references an unknown block", gaps=gaps)
        proposals.append(Proposal(item.item_id, judgment, candidate.get("text"), evidence))
    return Generation("done", value=tuple(proposals))


class RegenerateReviewSystem:
    def __init__(self, client: ModelClient, *, checkpoint_dir: Path | None = None) -> None:
        self._client = client
        self._checkpoint_dir = checkpoint_dir

    def review(self, request: ReviewRequest) -> ReviewResult:
        started = perf_counter()
        session = self._client.session(
            **asdict(request.budget), checkpoint_dir=self._checkpoint_dir,
        )
        generation = _regenerate(request, session)
        usage = session.usage
        return ReviewResult(
            proposals=generation.value if generation.state == "done" else (),
            cost=Cost(
                usage.input_tokens, usage.output_tokens, usage.estimated_amount,
                self._client.config.model_id,
            ),
            timing=Timing(usage.model_seconds, perf_counter() - started),
            state=generation.state,
            used_ledger=False,
            gaps=generation.gaps,
            error=generation.reason,
        )
