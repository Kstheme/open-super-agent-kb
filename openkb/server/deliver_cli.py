import argparse
from dataclasses import asdict
from functools import partial
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from openkb.core.delivery import MAX_STEPS, execute_delivery
from openkb.ingest.ledger import Ledger
from openkb.ingest.sqlite import SqliteStore
from openkb.profiles.word_accessibility import CHECKLIST_TITLE, SKELETON, find_coverage


def configure(commands) -> argparse.ArgumentParser:
    deliver = commands.add_parser("deliver", help="Request and inspect a Word self-check")
    subcommands = deliver.add_subparsers(dest="deliver_command", required=True)
    start = subcommands.add_parser("run", help="Request a Section 508 Word self-check")
    start.add_argument("message")
    start.add_argument("--source", action="append", required=True, help="Ingested source version ID")
    start.add_argument("--max-steps", type=int, default=MAX_STEPS)
    show = subcommands.add_parser("show", help="Read a persisted delivery task")
    show.add_argument("task")
    propose = subcommands.add_parser("propose", help="Record a proposed slot for human review")
    propose.add_argument("task")
    propose.add_argument("title")
    for command in (start, show, propose):
        command.add_argument("--database", type=Path, required=True)
    return deliver


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        store = SqliteStore(args.database)
        if args.deliver_command == "show":
            data = store.delivery(args.task)
        elif args.deliver_command == "propose":
            title = args.title.strip()
            if not title:
                raise ValueError("A proposed slot must contain a title")
            data = {"id": uuid4().hex, "task_id": args.task, "title": title, "state": "proposed"}
            store.save_skeleton_proposal(data)
        else:
            message = args.message.strip()
            if not message:
                raise ValueError("A delivery request must contain a message")
            source_ids = tuple(dict.fromkeys(args.source))
            ledger = Ledger(store)
            sources = tuple(ledger.version(version_id) for version_id in source_ids)
            result = execute_delivery(
                SKELETON,
                partial(find_coverage, sources=sources, ledger=ledger, claims=store),
                max_steps=args.max_steps,
            )
            task_id = uuid4().hex
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
            data = {
                "id": task_id,
                "kind": "deliver",
                "message": message,
                "sources": source_ids,
                "skeleton": [asdict(slot) for slot in SKELETON],
                "artifact": artifact,
                "skeleton_proposals": [],
                **result,
            }
            store.save_delivery(data)
    except (KeyError, OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return int(args.deliver_command == "run" and data["state"] == "paused")
