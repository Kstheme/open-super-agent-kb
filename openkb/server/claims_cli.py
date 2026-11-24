import argparse
from dataclasses import asdict
import json
from pathlib import Path

from openkb.agent.claim_extraction import extract_claims
from openkb.agent.model import EndpointError, ModelClient, load_model_config
from openkb.ingest.ledger import Ledger
from openkb.ingest.sqlite import SqliteStore


def configure(commands) -> argparse.ArgumentParser:
    claims = commands.add_parser("claims", help="Extract and inspect compliance claims")
    subcommands = claims.add_subparsers(dest="claims_command", required=True)
    extract = subcommands.add_parser("extract", help="Extract claims from an imported policy version")
    extract.add_argument("--config", type=Path, help="Override ~/.openkb/config.toml")
    listing = subcommands.add_parser("list", help="List saved claims for a document version")
    for command in (extract, listing):
        command.add_argument("version")
        command.add_argument("--database", type=Path, required=True)
    return claims


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        store = SqliteStore(args.database)
        if args.claims_command == "list":
            data = {"claims": [asdict(claim) for claim in store.claims(args.version)]}
        else:
            client = ModelClient(load_model_config(args.config))
            result = extract_claims(Ledger(store), store, client, args.version,
                                    checkpoint_dir=args.database.parent / "claim-checkpoints")
            data = asdict(result)
    except (EndpointError, KeyError, OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return int(data.get("state", "done") != "done")
