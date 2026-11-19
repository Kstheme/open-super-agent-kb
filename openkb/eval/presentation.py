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


def _number(value: int | float | None) -> str:
    return "不适用" if value is None else f"{value:.6g}"


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


def compare_reports(report_a: dict, report_b: dict) -> str:
    comparable = report_a["goldset_hash"] == report_b["goldset_hash"]
    lines = [
        f"A = {report_a['system']} / {report_a['goldset_hash']}",
        f"B = {report_b['system']} / {report_b['goldset_hash']}",
    ]
    if not comparable:
        lines.append("警告 · gold set 哈希不同，两份报告不可比。")

    lines.append("指标 | A | B | 差值 (B - A)")
    for label, path in _METRICS:
        a = _metric_value(report_a.get("aggregate"), path)
        b = _metric_value(report_b.get("aggregate"), path)
        delta = f"{b - a:+.6g}" if comparable and a is not None and b is not None else "不适用"
        lines.append(f"{label} | {_number(a)} | {_number(b)} | {delta}")
    return "\n".join(lines)


def render_run_summary(report: RunReport) -> str:
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
            f"{label} = {_number(_metric_value(aggregate, path))}" for label, path in _METRICS
        )

    for index, run in enumerate(report.runs, start=1):
        for metric, ids in run.excluded_cases.items():
            if ids:
                lines.append(f"运行 {index} · {metric} 不适用 · {', '.join(ids)}")
        if run.incomplete_cases:
            lines.append(f"运行 {index} · 未完成 · {', '.join(run.incomplete_cases)}")

    if report.system == "noop":
        lines.append("建议性结论 · 本次为 M0 零提案基线，用于校验评测链路。")
    else:
        lines.append("建议性结论 · 用同一 gold set 的对照报告核对召回与成本后，由人决定取舍。")
    return "\n".join(lines)
