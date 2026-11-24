import argparse
from dataclasses import replace
from os import fsync
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

from openkb.agent.model import EndpointError, ModelClient, load_model_config
from openkb.eval.goldset import load_goldset
from openkb.eval.noop import NoopReviewSystem
from openkb.eval.presentation import compare_reports, load_report, render_run_summary
from openkb.eval.regenerate import REGENERATE_NOTE, RegenerateReviewSystem
from openkb.eval.runner import run_goldset
from openkb.eval.thresholds import load_thresholds

from . import claims_cli, deliver_cli, ingest_cli


def _write_report(path: Path, content: str) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    started = perf_counter()
    parser = argparse.ArgumentParser(prog="openkb")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = ingest_cli.configure(commands)
    claims = claims_cli.configure(commands)
    deliver = deliver_cli.configure(commands)
    evaluate = commands.add_parser("eval", help="Evaluate a review system")
    eval_commands = evaluate.add_subparsers(dest="eval_command", required=True)
    run = eval_commands.add_parser("run", help="Run a review system against a gold set")
    run.add_argument("--goldset", type=Path, required=True)
    run.add_argument("--system", choices=("noop", "regenerate"), default="noop")
    run.add_argument("--runs", type=int, default=1)
    run.add_argument("--out", type=Path, default=Path("report.json"))
    run.add_argument("--config", type=Path, help="Override ~/.openkb/config.toml")
    run.add_argument("--reference-report", type=Path, help="Ledger report for the advisory summary")
    compare = eval_commands.add_parser("compare", help="Compare two evaluation reports")
    compare.add_argument("--report-a", type=Path, required=True)
    compare.add_argument("--report-b", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "ingest":
        return ingest_cli.run(args, ingest)

    if args.command == "claims":
        return claims_cli.run(args, claims)

    if args.command == "deliver":
        return deliver_cli.run(args, deliver)
    if args.eval_command == "compare":
        try:
            print(compare_reports(load_report(args.report_a), load_report(args.report_b)))
        except (OSError, ValueError) as error:
            compare.error(f"Cannot compare reports: {error}")
        return 0

    if args.runs < 1:
        run.error("--runs must be at least 1")
    try:
        thresholds = load_thresholds(args.config)
    except (OSError, ValueError) as error:
        run.error(f"Invalid evaluation configuration: {error}")
    try:
        goldset = load_goldset(args.goldset)
    except (OSError, ValueError, KeyError, TypeError) as error:
        run.error(f"Invalid gold set: {error}")

    try:
        reference = load_report(args.reference_report) if args.reference_report is not None else None
    except (OSError, ValueError) as error:
        run.error(f"Invalid reference report: {error}")

    price_table_version = None
    notes = ()
    if args.system == "regenerate":
        try:
            config = load_model_config(args.config)
            client = ModelClient(config)
            client.preflight()
        except (EndpointError, ValueError) as error:
            run.error(str(error))
        system = RegenerateReviewSystem(client)
        price_table_version = config.price_table_version
        notes = (REGENERATE_NOTE,)
    else:
        system = NoopReviewSystem()

    report = run_goldset(
        goldset, system, system_name=args.system, runs=args.runs,
        price_table_version=price_table_version, notes=notes,
    )
    report = replace(report, thresholds=thresholds)
    try:
        _write_report(args.out, report.to_json())
    except (OSError, ValueError) as error:
        run.error(f"Cannot write report: {error}")
    elapsed = perf_counter() - started
    print(render_run_summary(report, reference=reference))
    print(f"命令墙钟时间（含报告写入，秒） = {elapsed:.6g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
