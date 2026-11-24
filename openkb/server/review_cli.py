import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from openkb.agent.model import EndpointError, ModelClient, load_model_config
from openkb.agent.review import ReviewCandidate, judge_change
from openkb.core.delivery import MAX_STEPS, MAX_TOKENS, MAX_WALL_SECONDS
from openkb.core.review import trace_review
from openkb.ingest.ledger import EvidenceValidator, Ledger, StoreError
from openkb.ingest.model import Evidence, Version
from openkb.ingest.sqlite import SqliteStore, open_delivery_store


def configure(commands) -> argparse.ArgumentParser:
    review = commands.add_parser("review", help="Review a source revision against a delivered checklist")
    subcommands = review.add_subparsers(dest="review_command", required=True)
    start = subcommands.add_parser("run", help="Import and review a new version of an evidenced source")
    start.add_argument("artifact", help="Delivered checklist ID")
    start.add_argument("before_version", help="Previously evidenced source version ID")
    start.add_argument("new_file", type=Path, help="File containing the new source version")
    start.add_argument("--config", type=Path, help="Override ~/.openkb/config.toml")
    show = subcommands.add_parser("show", help="Read a persisted review")
    show.add_argument("review", help="Review ID")
    for command in (start, show):
        command.add_argument("--database", type=Path, required=True)
    return review


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



def _judge_affected(
    report: dict, source: Version, validator: EvidenceValidator, store: SqliteStore, config: Path | None,
) -> None:
    jobs = []
    for entry in report["affected"]:
        changes = entry["changes"]
        candidates = {
            block["id"]: block for change in changes for block in change["candidate_blocks"]
        }
        ambiguous = any(change["status"] == "ambiguous" for change in changes)
        if candidates and not ambiguous:
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
        client.preflight()
    except (EndpointError, ValueError) as error:
        report.update(state="failed", gaps=pending, error=str(error))
        return

    session = client.session(
        max_steps=MAX_STEPS, max_tokens=MAX_TOKENS, max_wall_seconds=MAX_WALL_SECONDS,
        checkpoint_dir=store.path.parent / "paused",
    )
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
        if judgment["judgment"] == "material":
            try:
                proofs = tuple(validator.validate(**citation) for citation in judgment["evidence"])
            except (KeyError, ValueError) as error:
                report.update(state="failed", gaps=pending[index:], error=str(error))
                break
            for proof in proofs:
                store.save_evidence(proof)
            report["proposals"].append({
                "id": uuid4().hex,
                "item_id": item_id,
                "claim_id": entry["claim_id"],
                "status": "pending",
                "old_item": item,
                "revised_text": judgment["revised_text"].strip(),
                "revised_method": judgment["revised_method"].strip(),
                "evidence": [_located(proof, source, blocks[proof.block_id]) for proof in proofs],
            })
        reason = judgment["reason"].strip()
        if judgment["judgment"] == "needs_human":
            report["judgments"].append(_human_review(entry, reason))
        else:
            report["judgments"].append({
                "item_id": item_id, "judgment": judgment["judgment"], "reason": reason,
            })
    report["usage"] = asdict(session.usage)


def _run_review(args: argparse.Namespace, store: SqliteStore) -> dict:
    artifact = store.artifact(args.artifact)
    if not any(
        evidence["version_id"] == args.before_version
        for item in artifact["items"] for evidence in item["evidence"]
    ):
        raise ValueError("The selected source version is not used by this artifact")

    ledger = Ledger(store)
    before = ledger.version(args.before_version)
    after = ledger.add(args.new_file, before.document_id)
    alignment = ledger.align(before.id, after.id)
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
        "before_version": before.id,
        "after_version": after.id,
        "normalization_version": alignment["normalization_version"],
        **trace_review(artifact["items"], alignment, rebase),
        "judgments": [],
        "proposals": [],
        "model_id": None,
        "usage": {"steps": 0, "input_tokens": 0, "output_tokens": 0, "estimated_amount": None, "model_seconds": 0},
        "gaps": [],
        "error": None,
    }
    _judge_affected(report, after, validator, store, args.config)
    store.save_review(report)
    return report


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        with open_delivery_store(args.database) as store:
            report = store.review(args.review) if args.review_command == "show" else _run_review(args, store)
    except (KeyError, OSError, ValueError, StoreError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(args.review_command == "run" and report["state"] != "done")
