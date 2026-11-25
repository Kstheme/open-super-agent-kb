import argparse
import json
from pathlib import Path

from openkb.ingest.ledger import StoreError
from openkb.ingest.sqlite import open_delivery_store

from .reviewing import start_review


def configure(commands) -> argparse.ArgumentParser:
    review = commands.add_parser("review", help="Review a source revision against a delivered checklist")
    subcommands = review.add_subparsers(dest="review_command", required=True)
    start = subcommands.add_parser("run", help="Import and review a new version of an evidenced source")
    start.add_argument("artifact", help="Delivered checklist ID")
    start.add_argument("before_version", help="Previous version ID of a source used by the checklist")
    start.add_argument("new_file", type=Path, help="File containing the new source version")
    start.add_argument("--config", type=Path, help="Override ~/.openkb/config.toml")
    show = subcommands.add_parser("show", help="Read a persisted review")
    show.add_argument("review", help="Review ID")
    accept = subcommands.add_parser("accept", help="Accept a proposal and create a new checklist version")
    reject = subcommands.add_parser("reject", help="Reject a proposal while keeping the checklist")
    for decision in (accept, reject):
        decision.add_argument("review", help="Review ID")
        decision.add_argument("proposal", help="Proposal ID")
    reject.add_argument("--reason", help="Optional reason to keep in the ledger")
    artifact = subcommands.add_parser("artifact", help="Read the current or a historical checklist version")
    artifact.add_argument("artifact", help="Delivered checklist ID")
    artifact.add_argument("--version", type=int, help="Checklist version number")
    for command in (start, show, accept, reject, artifact):
        command.add_argument("--database", type=Path, required=True)
    return review


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        with open_delivery_store(args.database) as store:
            command = args.review_command
            if command == "run":
                report = start_review(store, args.artifact, args.before_version, args.new_file, config=args.config)
            elif command == "show":
                report = store.review(args.review)
            elif command == "artifact":
                report = store.artifact(args.artifact, args.version)
            else:
                report = store.decide_review(
                    args.review, args.proposal, accept=command == "accept", reason=getattr(args, "reason", None),
                )
    except (KeyError, OSError, ValueError, StoreError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(args.review_command == "run" and report["state"] != "done")
