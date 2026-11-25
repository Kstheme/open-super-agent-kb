import argparse
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4

from openkb.core.delivery import DeliveryConflict, MAX_STEPS
from openkb.ingest.ledger import StoreError
from openkb.ingest.sqlite import SqliteStore, open_delivery_store
from openkb.profiles.word_accessibility import SKELETON, supports_slot

from .delivery import start_delivery


def configure(commands) -> argparse.ArgumentParser:
    deliver = commands.add_parser("deliver", help="Request and inspect a Word self-check")
    subcommands = deliver.add_subparsers(dest="deliver_command", required=True)
    start = subcommands.add_parser("run", help="Request a Section 508 Word self-check")
    start.add_argument("message", help="Request text to retain with the fixed Section 508 Word task")
    start.add_argument("--source", action="append", required=True, help="Ingested source version ID")
    start.add_argument("--max-steps", type=int, default=MAX_STEPS)
    show = subcommands.add_parser("show", help="Read a persisted delivery task")
    show.add_argument("task")
    propose = subcommands.add_parser("propose", help="Record a proposed slot for human review")
    propose.add_argument("task")
    propose.add_argument("title")
    flag = subcommands.add_parser("flag-conflict", help="Flag an unresolved conflict for a slot and source")
    flag.add_argument("slot", choices=tuple(slot.id for slot in SKELETON))
    flag.add_argument("source", help="Ingested source version ID")
    flag.add_argument("reason")
    resolve = subcommands.add_parser("resolve-conflict", help="Record that a conflict has been resolved")
    resolve.add_argument("conflict")
    for command in (start, show, propose, flag, resolve):
        command.add_argument("--database", type=Path, required=True)
    return deliver


def _dispatch(args: argparse.Namespace, store: SqliteStore) -> dict:
    if args.deliver_command == "show":
        return store.delivery(args.task)
    if args.deliver_command == "propose":
        title = args.title.strip()
        if not title:
            raise ValueError("A proposed slot must contain a title")
        proposal = {"id": uuid4().hex, "task_id": args.task, "title": title, "state": "proposed"}
        store.save_skeleton_proposal(proposal)
        return proposal
    if args.deliver_command == "flag-conflict":
        reason = args.reason.strip()
        if not reason:
            raise ValueError("A conflict must have a reason")
        source = store.version(args.source)
        if not supports_slot(source, args.slot):
            raise ValueError(f"{source.original_name} is not a source for slot {args.slot}")
        conflict = DeliveryConflict(uuid4().hex, args.slot, source.id, reason)
        store.flag_delivery_conflict(conflict)
        return asdict(conflict)
    if args.deliver_command == "resolve-conflict":
        return asdict(store.resolve_delivery_conflict(args.conflict))
    return start_delivery(store, args.message, args.source, max_steps=args.max_steps)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        with open_delivery_store(args.database) as store:
            data = _dispatch(args, store)
    except (KeyError, OSError, ValueError, StoreError) as error:
        parser.error(str(error))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return int(args.deliver_command == "run" and data["state"] == "paused")
