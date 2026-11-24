from dataclasses import asdict
from pathlib import Path


def configure(subcommands) -> None:
    add = subcommands.add_parser("evidence-add", help="Bind an exact quote to a version block")
    add.add_argument("version")
    add.add_argument("block")
    add.add_argument("--quote", required=True)
    add.add_argument("--quote-start", type=int, help="Zero-based character offset within the block")
    show = subcommands.add_parser("evidence-show", help="Locate saved evidence in its original")
    show.add_argument("evidence")
    for command in (add, show):
        command.add_argument("--database", type=Path, required=True)


def run(args, ledger) -> dict:
    if args.ingest_command == "evidence-add":
        evidence = ledger.add_evidence(
            args.version, args.block, args.quote, quote_start=args.quote_start,
        )
        return asdict(evidence)
    return asdict(ledger.locate_evidence(args.evidence))
