import json
import math
from dataclasses import asdict
from pathlib import Path

from .report import RunReport, validate_report_versions


_METRICS = (
    ("召回率", ("recall",)),
    ("误报率", ("false_positive_rate",)),
    ("总墙钟时间（秒）", ("timing", "total_seconds")),
    ("模型耗时（秒）", ("timing", "model_seconds")),
    ("输入 token", ("cost", "input_tokens")),
    ("输出 token", ("cost", "output_tokens")),
    ("估算金额", ("cost", "estimated_amount")),
    ("每条正确检出的估算金额", ("cost", "amount_per_correct_item")),
)
_TOKEN_METRICS = {("cost", "input_tokens"), ("cost", "output_tokens")}
_COMPARISON_VERSIONS = ("harness_version", "goldset_hash", "profile_version", "model_id", "price_table_version")


def _number(value: int | float | None, *, exact: bool, signed: bool = False) -> str:
    if value is None:
        return "不适用"
    sign = "+" if signed else ""
    if exact:
        return format(value, sign).removesuffix(".0")
    return format(value, f"{sign}.6g")


def _metric_value(aggregate: object, path: tuple[str, ...]) -> int | float | None:
    value = aggregate
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"Report is missing aggregate.{'.'.join(path)}")
        value = value[key]
    if value is None or type(value) is int or (type(value) is float and math.isfinite(value)):
        return value
    raise ValueError(f"Invalid report metric aggregate.{'.'.join(path)}")


def load_report(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError(f"Report {path} must contain a JSON object")
    try:
        validate_report_versions(report)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    return report


def _quality_comparable(ledger: dict, opponent: dict) -> bool:
    runs = ledger.get("runs")
    expected = opponent.get("runs")
    return (
        ledger.get("model_id") is not None
        and all(ledger[field] == opponent[field] for field in _COMPARISON_VERSIONS)
        and isinstance(runs, (list, tuple)) and bool(runs)
        and isinstance(expected, (list, tuple)) and len(runs) == len(expected)
    )


def _quality_gate(ledger: dict, opponent: dict, comparable: bool) -> str:
    if not comparable:
        return "M3 质量门槛 · 不可比。报告版本、模型或运行次数须一致。"
    a, b = ledger.get("aggregate"), opponent.get("aggregate")
    recall, baseline_recall = _metric_value(a, ("recall",)), _metric_value(b, ("recall",))
    false_alarms = _metric_value(a, ("false_positive_rate",))
    baseline_false_alarms = _metric_value(b, ("false_positive_rate",))
    if None in (recall, baseline_recall, false_alarms, baseline_false_alarms):
        return "M3 质量门槛 · 不可比。缺少有效的召回率或误报率。"
    decision = "通过" if recall >= baseline_recall and false_alarms <= baseline_false_alarms else "未通过"
    return f"M3 质量门槛 · {decision}。召回率须不低于 regenerate，误报率须不高于 regenerate。"


def compare_reports(report_a: dict, report_b: dict) -> str:
    same_goldset = report_a["goldset_hash"] == report_b["goldset_hash"]
    by_system = {report["system"]: report for report in (report_a, report_b)}
    m3_comparison = by_system.keys() == {"ledger", "regenerate"}
    comparable = (same_goldset if not m3_comparison
                  else _quality_comparable(by_system["ledger"], by_system["regenerate"]))
    lines = [
        f"A = {report_a['system']} / {report_a['goldset_hash']}",
        f"B = {report_b['system']} / {report_b['goldset_hash']}",
    ]
    if not same_goldset:
        lines.append("警告 · gold set 哈希不同，两份报告不可比。")

    lines.append("指标 | A | B | 差值 (B - A)")
    for label, path in _METRICS:
        a = _metric_value(report_a.get("aggregate"), path)
        b = _metric_value(report_b.get("aggregate"), path)
        exact = path in _TOKEN_METRICS
        delta = b - a if comparable and a is not None and b is not None else None
        lines.append(
            f"{label} | {_number(a, exact=exact)} | {_number(b, exact=exact)}"
            f" | {_number(delta, exact=exact, signed=True)}"
        )
    if m3_comparison:
        lines.append(_quality_gate(by_system["ledger"], by_system["regenerate"], comparable))
        if any(_metric_value(report.get("aggregate"), ("cost", "estimated_amount")) is None
               for report in by_system.values()):
            lines.append("费用比较 · 不可比。至少一份报告缺少估算费用。")
    return "\n".join(lines)


def _advice(report: RunReport, reference: dict | None) -> str:
    if report.system == "noop":
        return "本次为 M0 零提案基线，用于校验评测链路。"
    if report.system != "regenerate" or reference is None:
        return "用同一 gold set 的对照报告核对召回与成本后，由人决定取舍。"

    if reference["system"] in ("noop", "regenerate") or any(
        reference[field] != getattr(report, field) for field in _COMPARISON_VERSIONS
    ):
        return "对照系统或版本不匹配，结果不可比。"

    if report.aggregate is None or report.aggregate.recall is None:
        return "缺少可比较的召回率或成本，由人复核对照结果。"

    try:
        baseline_recall = _metric_value(reference.get("aggregate"), ("recall",))
        baseline_cost = _metric_value(reference.get("aggregate"), ("cost", "estimated_amount"))
    except ValueError:
        return "对照报告缺少有效的召回率或成本，由人复核对照结果。"

    opponent_cost = report.aggregate.cost.estimated_amount
    if baseline_recall is None or baseline_cost is None or opponent_cost is None:
        return "缺少可比较的召回率或成本，由人复核对照结果。"

    thresholds = report.thresholds
    close_recall = report.aggregate.recall >= baseline_recall - thresholds.recall_tolerance
    lower_cost = opponent_cost < baseline_cost and opponent_cost <= baseline_cost * thresholds.cheaper_cost_ratio
    if close_recall and lower_cost:
        return "对手达到召回率与低成本阈值，可考虑让账本只提供定位。最终取舍由人决定。"
    return "对手未同时达到召回率和低成本阈值，由人复核对照结果。"


def render_run_summary(report: RunReport, *, reference: dict | None = None) -> str:
    thresholds = report.thresholds
    lines = [
        f"M0 · system={report.system} · model_id={report.model_id or '不适用'}",
        f"goldset_hash={report.goldset_hash}",
        f"profile_version={report.profile_version} · "
        f"price_table_version={report.price_table_version or '不适用'}",
        f"判定阈值 · recall_tolerance={thresholds.recall_tolerance!r} · "
        f"cheaper_cost_ratio={thresholds.cheaper_cost_ratio!r}",
    ]
    if report.aggregate is not None:
        aggregate = asdict(report.aggregate)
        lines.extend(
            f"{label} = {_number(_metric_value(aggregate, path), exact=path in _TOKEN_METRICS)}"
            for label, path in _METRICS
        )

    for index, run in enumerate(report.runs, start=1):
        for metric, ids in run.excluded_cases.items():
            if ids:
                lines.append(f"运行 {index} · {metric} 不适用 · {', '.join(ids)}")
        if run.incomplete_cases:
            lines.append(f"运行 {index} · 未完成 · {', '.join(run.incomplete_cases)}")

    lines.append(f"建议性结论 · {_advice(report, reference)}")
    return "\n".join(lines)
