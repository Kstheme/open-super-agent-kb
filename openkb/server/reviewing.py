"""Run and persist the evidence-based review shared by CLI and Web."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from time import monotonic
from uuid import uuid4

from openkb.agent.model import EndpointError, ModelClient, load_model_config
from openkb.agent.review import ReviewCandidate, eligible_candidates, judge_change, proposal_evidence
from openkb.core.delivery import MAX_STEPS, MAX_TOKENS, MAX_WALL_SECONDS
from openkb.core.review import trace_blocks
from openkb.ingest.ledger import EvidenceValidator, Ledger
from openkb.ingest.model import Evidence, Version
from openkb.ingest.sqlite import SqliteStore


def _located(proof: Evidence, version: Version, block: dict) -> dict:
    return {
        **asdict(proof),
        "document_id": version.document_id,
        "original_name": version.original_name,
        "content_hash": version.content_hash,
        "locator": block["locator"],
    }


def _human_review(entry: dict, reason: str) -> dict:
    lost_evidence = []
    for change in entry["changes"]:
        proof = change["old_evidence"]
        if change["status"] == "ambiguous":
            continue
        if not any(proof["quote"] in block["text"] for block in change["candidate_blocks"]):
            lost_evidence.append(proof)
    return {
        "item_id": entry["item_id"],
        "claim_id": entry["claim_id"],
        "judgment": "needs_human",
        "reason": reason,
        "original_statement": entry["item"]["check"],
        "lost_evidence": lost_evidence,
    }


def _pause_review(report: dict, gaps: tuple[str, ...]) -> None:
    report.update(state="paused", gaps=gaps, error="wall clock budget exhausted")


def _judge_affected(
    report: dict, source: Version, validator: EvidenceValidator, store: SqliteStore,
    config: Path | None, deadline: float,
) -> None:
    jobs = []
    for entry in report["affected"]:
        changes = entry["changes"]
        ambiguous = any(change["status"] == "ambiguous" for change in changes)
        candidates = eligible_candidates(
            ((block["id"], block) for change in changes for block in change["candidate_blocks"]),
            ambiguous=ambiguous,
        )
        if candidates:
            jobs.append((entry, candidates))
        else:
            reason = "新版证据的块对齐存在歧义。" if ambiguous else "新版缺少可核对的替代依据。"
            report["judgments"].append(_human_review(entry, reason))
    if not jobs:
        return

    pending = tuple(entry["item_id"] for entry, _ in jobs)
    try:
        client = ModelClient(load_model_config(config))
        report["model_id"] = client.config.model_id
    except ValueError as error:
        report.update(state="failed", gaps=pending, error=str(error))
        return
    remaining = deadline - monotonic()
    if remaining <= 0:
        _pause_review(report, pending)
        return
    try:
        client.preflight(timeout=min(5, remaining))
    except EndpointError as error:
        if monotonic() >= deadline:
            _pause_review(report, pending)
        else:
            report.update(state="failed", gaps=pending, error=str(error))
        return
    remaining = deadline - monotonic()
    if remaining <= 0:
        _pause_review(report, pending)
        return

    session = client.session(
        max_steps=MAX_STEPS, max_tokens=MAX_TOKENS, max_wall_seconds=remaining,
        checkpoint_dir=store.path.parent / "paused",
    )
    retained = {}
    for rebase in report["rebased_evidence"]:
        retained.setdefault(rebase["item_id"], []).append(rebase["new_evidence"])
    for index, (entry, blocks) in enumerate(jobs):
        item = entry["item"]
        item_id = entry["item_id"]
        generation = judge_change(
            session,
            item_text=item["check"],
            item_method=item["method"],
            old_evidence=tuple(evidence["quote"] for evidence in item["evidence"]),
            candidates=tuple(
                ReviewCandidate(block_id, block["text"], tuple(block["heading_path"]))
                for block_id, block in blocks.items()
            ),
            gaps=pending[index:],
        )
        if generation.state != "done":
            report.update(state=generation.state, gaps=generation.gaps, error=generation.reason)
            break

        judgment = generation.value
        if monotonic() >= deadline:
            _pause_review(report, pending[index:])
            break
        if judgment["judgment"] == "material":
            try:
                proofs = tuple(validator.validate(**citation) for citation in judgment["evidence"])
            except (KeyError, ValueError) as error:
                report.update(state="failed", gaps=pending[index:], error=str(error))
                break
            for proof in proofs:
                store.save_evidence(proof)
            if monotonic() >= deadline:
                _pause_review(report, pending[index:])
                break
            evidence = proposal_evidence(
                (_located(proof, source, blocks[proof.block_id]) for proof in proofs),
                retained.get(item_id, ()),
                identity=lambda proof: proof["id"],
            )
            report["proposals"].append({
                "id": uuid4().hex,
                "item_id": item_id,
                "claim_id": entry["claim_id"],
                "status": "pending",
                "old_item": item,
                "revised_text": judgment["revised_text"].strip(),
                "revised_method": judgment["revised_method"].strip(),
                "evidence": list(evidence),
            })
        reason = judgment["reason"].strip()
        if judgment["judgment"] == "needs_human":
            report["judgments"].append(_human_review(entry, reason))
        else:
            report["judgments"].append({
                "item_id": item_id, "judgment": judgment["judgment"], "reason": reason,
            })
    report["usage"] = asdict(session.usage)


def _trace_source(
    items: list[dict], citations_by_version: dict[str, list[tuple[str, dict]]], after: str, ledger: Ledger,
    rebase: Callable[[dict, dict], dict | None],
) -> dict:
    changes = defaultdict(list)
    rebased = []
    warnings = []
    for cited_version, citations in citations_by_version.items():
        if cited_version == after:
            continue
        traces, alignment_warnings = trace_blocks(
            ledger.align(cited_version, after), (evidence["block_id"] for _, evidence in citations),
        )
        warnings.extend(alignment_warnings)
        for item_id, evidence in citations:
            block_id = evidence["block_id"]
            trace = traces[block_id]
            status = trace.status
            if status == "unchanged":
                located = rebase(evidence, trace.candidates[0])
                if located is not None:
                    rebased.append({"item_id": item_id, "old_evidence": evidence, "new_evidence": located})
                    continue
                status = "ambiguous"
            if trace.uncertain_replacement or trace.status == "unchanged":
                warnings.append({
                    "kind": "uncertain_evidence_alignment",
                    "before_block_ids": [block_id],
                    "after_block_ids": [block["id"] for block in trace.candidates],
                })
            changes[item_id].append({
                "old_evidence": evidence,
                "before_block": trace.before,
                "status": status,
                "candidate_blocks": trace.candidates,
            })
    return {
        "affected": [
            {"item_id": item["slot_id"], "claim_id": item["claim_id"], "item": item,
             "changes": changes[item["slot_id"]]}
            for item in items if item["slot_id"] in changes
        ],
        "rebased_evidence": rebased,
        "alignment_warnings": warnings,
    }


def start_review(
    store: SqliteStore, artifact_id: str, before_version: str, new_file: Path, *, config: Path | None = None,
) -> dict:
    deadline = monotonic() + MAX_WALL_SECONDS
    artifact = store.artifact(artifact_id)
    ledger = Ledger(store)
    before = ledger.version(before_version)
    citations_by_version = defaultdict(list)
    for item in artifact["items"]:
        for evidence in item["evidence"]:
            if evidence["document_id"] == before.document_id:
                citations_by_version[evidence["version_id"]].append((item["slot_id"], evidence))
    if not citations_by_version:
        raise ValueError("The selected source document is not used by this artifact")
    after = ledger.add(new_file, before.document_id)
    validator = EvidenceValidator(after)

    def rebase(old_evidence: dict, block: dict) -> dict | None:
        try:
            proof = validator.validate(block["id"], old_evidence["quote"], quote_start=old_evidence["quote_start"])
        except ValueError:
            return None
        store.save_evidence(proof)
        return _located(proof, after, block)

    report = {
        "id": uuid4().hex,
        "kind": "review",
        "state": "done",
        "artifact_id": artifact["id"],
        "artifact_version": artifact["version"],
        "before_version": before.id,
        "after_version": after.id,
        "normalization_version": after.normalization_version,
        **_trace_source(artifact["items"], citations_by_version, after.id, ledger, rebase),
        "judgments": [],
        "proposals": [],
        "model_id": None,
        "usage": {"steps": 0, "input_tokens": 0, "output_tokens": 0, "estimated_amount": None, "model_seconds": 0},
        "gaps": [],
        "error": None,
    }
    if monotonic() >= deadline:
        _pause_review(report, tuple(entry["item_id"] for entry in report["affected"]))
    else:
        _judge_affected(report, after, validator, store, config, deadline)
    store.save_review(report)
    return report
