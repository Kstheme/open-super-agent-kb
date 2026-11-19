import argparse
from pathlib import Path

from openkb.eval.goldset import load_goldset
from openkb.eval.noop import NoopReviewSystem
from openkb.eval.runner import run_goldset


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
    args = parser.parse_args(argv)
    if args.runs < 1:
        run.error("--runs must be at least 1")

    try:
        goldset = load_goldset(args.goldset)
    except (OSError, ValueError, KeyError, TypeError) as error:
        run.error(f"Invalid gold set: {error}")

    report = run_goldset(goldset, NoopReviewSystem(), system_name=args.system, runs=args.runs)
    try:
        args.out.write_text(report.to_json(), encoding="utf-8")
    except (OSError, ValueError) as error:
        run.error(f"Cannot write report: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
