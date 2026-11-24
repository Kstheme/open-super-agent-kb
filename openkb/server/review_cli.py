import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from openkb.core.review import trace_review
from openkb.ingest.ledger import EvidenceValidator, Ledger, StoreError
from openkb.ingest.sqlite import SqliteStore, open_delivery_store


def configure(commands) -> argparse.ArgumentParser:
    review = commands.add_parser("review", help="Review a source revision against a delivered checklist")
    subcommands = review.add_subparsers(dest="review_command", required=True)
    start = subcommands.add_parser("run", help="Import and review a new version of an evidenced source")
    start.add_argument("artifact", help="Delivered checklist ID")
    start.add_argument("before_version", help="Previously evidenced source version ID")
    start.add_argument("new_file", type=Path, help="File containing the new source version")
    show = subcommands.add_parser("show", help="Read a persisted review")
    show.add_argument("review", help="Review ID")
    for command in (start, show):
        command.add_argument("--database", type=Path, required=True)
    return review


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
        return {
            **asdict(proof),
            "document_id": after.document_id,
            "original_name": after.original_name,
            "content_hash": after.content_hash,
            "locator": block["locator"],
        }

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
    }
    store.save_review(report)
    return report


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        with open_delivery_store(args.database) as store:
            report = store.review(args.review) if args.review_command == "show" else _run_review(args, store)
    except (KeyError, OSError, ValueError, StoreError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
