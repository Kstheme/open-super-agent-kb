import json
from dataclasses import asdict, dataclass, field
from typing import Mapping

from .contract import ReviewResult
from .metrics import CaseMetrics, Metrics


def validate_report_versions(report: Mapping[str, object]) -> None:
    """Check the version envelope of a decoded report before it is used."""
    for field in (
        "harness_version", "goldset_hash", "system", "model_id", "profile_version",
        "price_table_version",
    ):
        if field not in report:
            raise ValueError(f"Report is missing {field}")
        value = report[field]
        if value is None and field in ("model_id", "price_table_version"):
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Report field {field} must be a non-empty string")


@dataclass(frozen=True)
class CaseReport:
    case_id: str
    result: ReviewResult
    metrics: CaseMetrics | None = None


@dataclass(frozen=True)
class RunSummary:
    cases: tuple[CaseReport, ...]
    aggregate: Metrics
    excluded_cases: dict[str, tuple[str, ...]]
    incomplete_cases: tuple[str, ...]


@dataclass(frozen=True)
class RunReport:
    harness_version: str
    goldset_hash: str
    system: str
    model_id: str | None
    profile_version: str
    price_table_version: str | None
    cases: tuple[CaseReport, ...]
    aggregate: Metrics | None = None
    runs: tuple[RunSummary, ...] = ()
    disagreement: dict[str, float | None] = field(default_factory=dict)

    def to_json(self) -> str:
        report = asdict(self)
        validate_report_versions(report)
        return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
