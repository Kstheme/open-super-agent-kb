from dataclasses import replace
from time import perf_counter

from .contract import Cost, ReviewRequest, ReviewResult, ReviewSystem, Timing
from .goldset import GoldSet
from .metrics import aggregate_cases, disagreement, median_runs, score_case
from .report import CaseReport, RunReport, RunSummary


HARNESS_VERSION = "1"


def _review_case(system: ReviewSystem, request: ReviewRequest) -> ReviewResult:
    started = perf_counter()
    try:
        result = system.review(request)
    except Exception as error:
        return ReviewResult(
            proposals=(),
            cost=Cost(None, None, None, None),
            timing=Timing(None, perf_counter() - started),
            state="failed",
            used_ledger=False,
            error=f"{type(error).__name__}: {error}",
        )
    elapsed = perf_counter() - started
    model_seconds = result.timing.model_seconds
    return replace(result, timing=Timing(model_seconds, max(elapsed, model_seconds or 0.0)))


def _run_once(goldset: GoldSet, system: ReviewSystem) -> RunSummary:
    reports = []
    scores = []
    excluded_recall = []
    excluded_false_positive_rate = []
    incomplete = []
    for case in goldset.cases:
        result = _review_case(system, case.request)
        metrics = score_case(result, case.annotations)
        reports.append(CaseReport(case.case_id, result, metrics))
        scores.append(metrics)
        if metrics.recall is None:
            excluded_recall.append(case.case_id)
        if metrics.false_positive_rate is None:
            excluded_false_positive_rate.append(case.case_id)
        if result.state != "done":
            incomplete.append(case.case_id)

    return RunSummary(
        tuple(reports), aggregate_cases(scores),
        {"recall": tuple(excluded_recall), "false_positive_rate": tuple(excluded_false_positive_rate)},
        tuple(incomplete),
    )


def run_goldset(
    goldset: GoldSet,
    system: ReviewSystem,
    *,
    system_name: str,
    price_table_version: str | None = None,
    runs: int = 1,
    notes: tuple[str, ...] = (),
) -> RunReport:
    if runs < 1:
        raise ValueError("runs must be at least 1")

    summaries = tuple(_run_once(goldset, system) for _ in range(runs))
    model_ids = {case.result.cost.model_id for summary in summaries for case in summary.cases} - {None}
    if len(model_ids) > 1:
        raise ValueError("A run report requires a single model_id")

    return RunReport(
        harness_version=HARNESS_VERSION,
        goldset_hash=goldset.goldset_hash,
        system=system_name,
        model_id=next(iter(model_ids), None),
        profile_version=goldset.profile_version,
        price_table_version=price_table_version,
        cases=summaries[0].cases,
        aggregate=median_runs([summary.aggregate for summary in summaries]),
        runs=summaries,
        disagreement={
            case.case_id: disagreement([summary.cases[index].result for summary in summaries], case.annotations)
            for index, case in enumerate(goldset.cases)
        },
        notes=notes,
    )
