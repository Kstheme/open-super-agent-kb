from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


@dataclass(frozen=True)
class EvalThresholds:
    recall_tolerance: float = 0.05
    cheaper_cost_ratio: float = 0.8


def load_thresholds(path: Path | None) -> EvalThresholds:
    config_path = path if path is not None else Path.home() / ".openkb" / "config.toml"
    try:
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)
    except FileNotFoundError:
        if path is not None:
            raise
        return EvalThresholds()

    eval_config = config.get("eval", {})
    if not isinstance(eval_config, dict):
        raise ValueError("eval must be a TOML table")
    values = eval_config.get("thresholds", {})
    if not isinstance(values, dict):
        raise ValueError("eval.thresholds must be a TOML table")

    defaults = EvalThresholds()
    return EvalThresholds(
        recall_tolerance=_ratio(values, "recall_tolerance", defaults.recall_tolerance),
        cheaper_cost_ratio=_ratio(values, "cheaper_cost_ratio", defaults.cheaper_cost_ratio),
    )


def _ratio(values: dict, key: str, default: float) -> float:
    value = values.get(key, default)
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError(f"eval.thresholds.{key} must be a number between 0 and 1")
    return float(value)
