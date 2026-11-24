import argparse
import sys
from pathlib import Path

from openkb.ingest.batch import Batch
from openkb.ingest.ledger import Ledger
from openkb.ingest.sqlite import SqliteStore


COMMANDS = ("batch", "resume", "status", "retry")


def configure(subcommands) -> None:
    batch = subcommands.add_parser("batch", help="Snapshot and import a directory")
    batch.add_argument("directory", type=Path)
    resume = subcommands.add_parser("resume", help="Import the remaining pending files")
    status = subcommands.add_parser("status", help="Read persisted batch progress and failures")
    retry = subcommands.add_parser("retry", help="Reprocess one failed file")
    for command in (batch, resume, status, retry):
        command.add_argument("--database", type=Path, required=True)
        if command is not batch:
            command.add_argument("batch_id")
        if command in (batch, resume):
            command.add_argument("--limit", type=int, help="Maximum files to attempt in this invocation")
    retry.add_argument("file", type=Path, help="Path to the replaced source file")


def _progress(batch_id: str, index: int, total: int, path: Path) -> None:
    print(f"Ingest {batch_id} {index}/{total}: {path}", file=sys.stderr, flush=True)


def run(args: argparse.Namespace, store: SqliteStore) -> dict:
    batch = Batch(Ledger(store), store, _progress)
    if args.ingest_command == "batch":
        if args.limit is not None and args.limit < 1:
            raise ValueError("File budget must be at least 1")
        batch_id = batch.start(args.directory)
        return batch.resume(batch_id, args.limit)
    if args.ingest_command == "status":
        return batch.status(args.batch_id)
    if args.ingest_command == "retry":
        return batch.resume(args.batch_id, retry=args.file)
    return batch.resume(args.batch_id, args.limit)
