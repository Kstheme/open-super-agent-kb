"""Adapt M0 snapshots and baseline provenance to the M3 review pipeline."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from openkb.agent.model import ModelClient
from openkb.agent.review import ReviewCandidate, eligible_candidates, judge_change, proposal_evidence
from openkb.core.review import align_blocks, trace_blocks

from .contract import Block, Cost, EvidenceReference, Proposal, ReviewRequest, ReviewResult, Timing


LEDGER_NOTE = "Ledger review uses the checklist's old evidence references to scope the review."


def _old_provenance(request: ReviewRequest) -> dict[str, tuple[EvidenceReference, ...]]:
    items = request.ledger.get("items") if isinstance(request.ledger, Mapping) else None
    if not isinstance(items, Mapping):
        raise ValueError("Ledger evaluation needs baseline item evidence")
    result = {}
    for item in request.artifact:
        entry = items.get(item.item_id)
        values = entry.get("evidence") if isinstance(entry, Mapping) else None
        if not isinstance(values, (tuple, list)) or not values:
            raise ValueError(f"Missing baseline evidence for {item.item_id}")
        refs = []
        for value in values:
            fields = ("document_id", "version_id", "block_key")
            if not isinstance(value, Mapping) or any(not isinstance(value.get(field), str) for field in fields):
                raise ValueError(f"Invalid baseline evidence for {item.item_id}")
            refs.append(EvidenceReference(*(value[field] for field in fields)))
        result[item.item_id] = tuple(dict.fromkeys(refs))
    return result


def _trace_block(block: Block) -> dict:
    return {
        "id": block.block_key,
        "block_key": block.block_key,
        "heading_path": block.heading_path,
        "ordinal": block.ordinal,
        "text": block.text,
        "clause": None,
        "kind": "block",
    }


@dataclass(frozen=True)
class _EvidenceReview:
    old_text: str
    after: EvidenceReference | None = None
    candidates: tuple[tuple[EvidenceReference, dict], ...] = ()
    ambiguous: bool = False


def _compare_evidence(
    request: ReviewRequest, basis: dict[str, tuple[EvidenceReference, ...]],
) -> dict[EvidenceReference, _EvidenceReview]:
    references = dict.fromkeys(reference for item in basis.values() for reference in item)
    after_versions = {document.document_id: document.version for document in request.corpus.after}
    result = {}
    for document in request.corpus.before:
        document_id = document.document_id
        before = document.version
        after = after_versions[document_id]
        old_blocks = {block.block_key: block for block in before.blocks}
        old_refs = {
            ref.block_key: ref for ref in references
            if ref.document_id == document_id and ref.version_id == before.version_id
        }
        for key in old_refs:
            if key not in old_blocks:
                raise ValueError("Baseline evidence references an unknown block")

        if before.version_id == after.version_id:
            for key, ref in old_refs.items():
                result[ref] = _EvidenceReview(old_blocks[key].text, after=ref)
            continue

        alignment = align_blocks(
            [_trace_block(block) for block in before.blocks],
            [_trace_block(block) for block in after.blocks],
        )
        traces, _ = trace_blocks(alignment, old_refs)
        for key, trace in traces.items():
            old_ref = old_refs[key]
            if trace.status == "unchanged":
                rebased = EvidenceReference(document_id, after.version_id, trace.candidates[0]["block_key"])
                result[old_ref] = _EvidenceReview(old_blocks[key].text, after=rebased)
                continue
            candidates = tuple(
                (EvidenceReference(document_id, after.version_id, block["block_key"]), block)
                for block in trace.candidates
            )
            result[old_ref] = _EvidenceReview(
                old_blocks[key].text, candidates=candidates, ambiguous=trace.status == "ambiguous",
            )
    if result.keys() != references.keys():
        raise ValueError("Baseline evidence references an unknown source version")
    return result


class LedgerReviewSystem:
    def __init__(self, client: ModelClient, *, checkpoint_dir: Path | None = None) -> None:
        self._client = client
        self._checkpoint_dir = checkpoint_dir

    def review(self, request: ReviewRequest) -> ReviewResult:
        started = perf_counter()
        session = self._client.session(**asdict(request.budget), checkpoint_dir=self._checkpoint_dir)

        def result(proposals=(), *, state="done", used_ledger=True, gaps=(), error=None):
            usage = session.usage
            return ReviewResult(
                proposals,
                Cost(usage.input_tokens, usage.output_tokens, usage.estimated_amount,
                     self._client.config.model_id),
                Timing(usage.model_seconds, perf_counter() - started),
                state, used_ledger, gaps, error,
            )

        try:
            basis = _old_provenance(request)
            changes = _compare_evidence(request, basis)
        except (KeyError, ValueError) as error:
            return result(
                state="failed", used_ledger=request.ledger is not None,
                gaps=tuple(item.item_id for item in request.artifact), error=str(error),
            )

        jobs = []
        for item in request.artifact:
            evidence = tuple(changes[ref] for ref in basis[item.item_id])
            blocks = eligible_candidates(
                (candidate for review in evidence for candidate in review.candidates),
                ambiguous=any(review.ambiguous for review in evidence),
            )
            if blocks:
                jobs.append((item, evidence, blocks))

        pending = tuple(item.item_id for item, _, _ in jobs)
        proposals = []
        for index, (item, evidence, blocks) in enumerate(jobs):
            # Request-local IDs disambiguate block keys that coincide across documents.
            candidates = {str(index): ref for index, ref in enumerate(blocks, start=1)}
            generation = judge_change(
                session,
                item_text=item.snapshot_text,
                old_evidence=tuple(review.old_text for review in evidence),
                candidates=tuple(
                    ReviewCandidate(candidate_id, blocks[ref]["text"], blocks[ref]["heading_path"])
                    for candidate_id, ref in candidates.items()
                ),
                gaps=pending[index:],
            )
            if generation.state != "done":
                return result(state=generation.state, gaps=generation.gaps, error=generation.reason)
            judgment = generation.value
            if judgment["judgment"] == "material":
                proposals.append(Proposal(
                    item.item_id, "material", judgment["revised_text"].strip(),
                    proposal_evidence(
                        (candidates[reference["block_id"]] for reference in judgment["evidence"]),
                        (review.after for review in evidence if review.after is not None),
                        identity=lambda reference: reference,
                    ),
                ))
            elif judgment["judgment"] == "immaterial":
                proposals.append(Proposal(item.item_id, "immaterial"))
        return result(tuple(proposals))
