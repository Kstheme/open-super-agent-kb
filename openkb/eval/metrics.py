from dataclasses import dataclass
from statistics import fmean, median
from typing import Iterable, Mapping, Sequence

from .contract import ReviewResult, Timing


Annotations = Sequence[Mapping[str, object]]


@dataclass(frozen=True)
class MetricCost:
    input_tokens: int | float | None
    output_tokens: int | float | None
    estimated_amount: float | None
    amount_per_correct_item: float | None


@dataclass(frozen=True)
class Metrics:
    recall: float | None
    false_positive_rate: float | None
    timing: Timing
    cost: MetricCost


@dataclass(frozen=True)
class CaseMetrics(Metrics):
    correct_items: int


def _rate(count: int, total: int) -> float | None:
    return count / total if total else None


def _material_counts(result: ReviewResult, annotations: Annotations) -> tuple[int, int, int, int]:
    positive = negative = correct = false_alarms = 0
    material = {
        proposal.item_id for proposal in result.proposals
        if result.state == "done" and proposal.judgment == "material"
    }
    for annotation in annotations:
        proposed = annotation["item_id"] in material
        if annotation["judgment"] == "material":
            positive += 1
            correct += proposed
        else:
            negative += 1
            false_alarms += proposed
    return positive, negative, correct, false_alarms


def recall(result: ReviewResult, annotations: Annotations) -> float | None:
    positive, _, correct, _ = _material_counts(result, annotations)
    return _rate(correct, positive)


def false_positive_rate(result: ReviewResult, annotations: Annotations) -> float | None:
    _, negative, _, false_alarms = _material_counts(result, annotations)
    return _rate(false_alarms, negative)


def latency(result: ReviewResult) -> Timing:
    return result.timing


def cost(result: ReviewResult, *, correct_items: int) -> MetricCost:
    usage = result.cost
    amount = usage.estimated_amount
    return MetricCost(
        usage.input_tokens, usage.output_tokens, amount,
        amount / correct_items if amount is not None and correct_items else None,
    )


def score_case(result: ReviewResult, annotations: Annotations) -> CaseMetrics:
    positive, negative, correct, false_alarms = _material_counts(result, annotations)
    return CaseMetrics(
        _rate(correct, positive), _rate(false_alarms, negative),
        latency(result), cost(result, correct_items=correct), correct,
    )


def _mean(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return fmean(known) if known else None


def _sum_if_complete(values: Iterable[int | float | None]) -> int | float | None:
    known = []
    for value in values:
        if value is None:
            return None
        known.append(value)
    return sum(known)


def aggregate_cases(cases: Sequence[CaseMetrics]) -> Metrics:
    amount = _sum_if_complete(case.cost.estimated_amount for case in cases)
    correct = sum(case.correct_items for case in cases)
    return Metrics(
        _mean(case.recall for case in cases),
        _mean(case.false_positive_rate for case in cases),
        Timing(
            _sum_if_complete(case.timing.model_seconds for case in cases),
            sum(case.timing.total_seconds for case in cases),
        ),
        MetricCost(
            _sum_if_complete(case.cost.input_tokens for case in cases),
            _sum_if_complete(case.cost.output_tokens for case in cases),
            amount,
            amount / correct if amount is not None and correct else None,
        ),
    )


def _median(values: Iterable[int | float | None]) -> int | float | None:
    known = [value for value in values if value is not None]
    return median(known) if known else None


def median_runs(runs: Sequence[Metrics]) -> Metrics:
    return Metrics(
        _median(run.recall for run in runs),
        _median(run.false_positive_rate for run in runs),
        Timing(
            _median(run.timing.model_seconds for run in runs),
            median(run.timing.total_seconds for run in runs),
        ),
        MetricCost(
            _median(run.cost.input_tokens for run in runs),
            _median(run.cost.output_tokens for run in runs),
            _median(run.cost.estimated_amount for run in runs),
            _median(run.cost.amount_per_correct_item for run in runs),
        ),
    )


def disagreement(results: Sequence[ReviewResult], annotations: Annotations) -> float | None:
    if len(results) < 2 or not annotations:
        return None

    judgments = [
        {proposal.item_id: proposal.judgment for proposal in result.proposals}
        if result.state == "done" else {}
        for result in results
    ]
    changed = sum(
        len({run.get(annotation["item_id"], "not_affected") for run in judgments}) > 1
        for annotation in annotations
    )
    return changed / len(annotations)
