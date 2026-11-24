import argparse
import json
from dataclasses import asdict
from pathlib import Path

from openkb.ingest.ledger import Ledger
from openkb.ingest.sqlite import SqliteStore

from . import evidence_cli
from . import batch_cli


def configure(commands) -> argparse.ArgumentParser:
    ingest = commands.add_parser("ingest", help="Import local documents and inspect their versions")
    subcommands = ingest.add_subparsers(dest="ingest_command", required=True)
    add = subcommands.add_parser("add", help="Import one document")
    add.add_argument("source", type=Path)
    add.add_argument("--document", help="Shared logical identity when versions have different file names")
    show = subcommands.add_parser("show", help="Inspect the imported version and its blocks")
    show.add_argument("version")
    align = subcommands.add_parser("align", help="Compare two versions without guessing duplicate matches")
    align.add_argument("before")
    align.add_argument("after")
    original = subcommands.add_parser("original", help="Export the retained original for a version")
    original.add_argument("version")
    original.add_argument("--out", type=Path, required=True)
    for command in (add, show, align, original):
        command.add_argument("--database", type=Path, required=True)
    evidence_cli.configure(subcommands)
    batch_cli.configure(subcommands)
    return ingest


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        store = SqliteStore(args.database)
        ledger = Ledger(store)
        if args.ingest_command in batch_cli.COMMANDS:
            result = batch_cli.run(args, store)
        elif args.ingest_command == "add":
            result = asdict(ledger.add(args.source, args.document))
            result["block_count"] = len(result.pop("blocks"))
        elif args.ingest_command == "show":
            result = asdict(ledger.version(args.version))
        elif args.ingest_command == "align":
            result = ledger.align(args.before, args.after)
        elif args.ingest_command.startswith("evidence-"):
            result = evidence_cli.run(args, ledger)
        else:
            data = ledger.original(args.version)
            with args.out.open("xb") as target:
                target.write(data)
            result = {"version": args.version, "path": str(args.out)}
    except (KeyError, OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.ingest_command in batch_cli.COMMANDS and result.get("interrupted"):
        return 130
    if args.ingest_command in batch_cli.COMMANDS and args.ingest_command != "status" and result["failures"]:
        return 1
    return 0
