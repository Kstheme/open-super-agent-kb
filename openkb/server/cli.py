import argparse
from dataclasses import replace
from pathlib import Path

from openkb.eval.goldset import load_goldset
from openkb.eval.noop import NoopReviewSystem
from openkb.eval.presentation import compare_reports, load_report, render_run_summary
from openkb.eval.runner import run_goldset
from openkb.eval.thresholds import load_thresholds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openkb")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("eval", help="Evaluate a review system")
    eval_commands = evaluate.add_subparsers(dest="eval_command", required=True)
    run = eval_commands.add_parser("run", help="Run a review system against a gold set")
    run.add_argument("--goldset", type=Path, required=True)
    run.add_argument("--system", choices=("noop",), default="noop")
    run.add_argument("--runs", type=int, default=1)
    run.add_argument("--out", type=Path, default=Path("report.json"))
    run.add_argument("--config", type=Path, help="Override ~/.openkb/config.toml")
    compare = eval_commands.add_parser("compare", help="Compare two evaluation reports")
    compare.add_argument("--report-a", type=Path, required=True)
    compare.add_argument("--report-b", type=Path, required=True)
    args = parser.parse_args(argv)

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

    report = run_goldset(goldset, NoopReviewSystem(), system_name=args.system, runs=args.runs)
    report = replace(report, thresholds=thresholds)
    try:
        args.out.write_text(report.to_json(), encoding="utf-8")
    except (OSError, ValueError) as error:
        run.error(f"Cannot write report: {error}")
    print(render_run_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
