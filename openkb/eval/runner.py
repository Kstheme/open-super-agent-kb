from time import perf_counter

from .contract import Cost, ReviewRequest, ReviewResult, ReviewSystem, Timing
from .goldset import GoldSet
from .report import CaseReport, RunReport


HARNESS_VERSION = "1"


def _review_case(system: ReviewSystem, request: ReviewRequest) -> ReviewResult:
    started = perf_counter()
    try:
        return system.review(request)
    except Exception as error:
        return ReviewResult(
            proposals=(),
            cost=Cost(None, None, None, None),
            timing=Timing(None, perf_counter() - started),
            state="failed",
            used_ledger=False,
            error=f"{type(error).__name__}: {error}",
        )


def run_goldset(
    goldset: GoldSet,
    system: ReviewSystem,
    *,
    system_name: str,
    price_table_version: str | None = None,
) -> RunReport:
    cases = tuple(
        CaseReport(case.case_id, _review_case(system, case.request)) for case in goldset.cases
    )
    model_ids = {case.result.cost.model_id for case in cases} - {None}
    if len(model_ids) > 1:
        raise ValueError("A run report requires a single model_id")
    return RunReport(
        harness_version=HARNESS_VERSION,
        goldset_hash=goldset.goldset_hash,
        system=system_name,
        model_id=next(iter(model_ids), None),
        profile_version=goldset.profile_version,
        price_table_version=price_table_version,
        cases=cases,
    )
