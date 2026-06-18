from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


def hour_bucket(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def apply_strategy_adjustments(forecast: dict, adjustments: dict[str, dict]) -> dict:
    """Apply bounded walk-forward calibration learned only from past predictions."""
    result = deepcopy(forecast)
    current = float(result["current_price"])
    for item in result["forecasts"]:
        adjustment = adjustments.get(str(item["key"]), {})
        if not adjustment.get("active"):
            item["learning"] = {"active": False, "samples": int(adjustment.get("samples", 0))}
            continue

        raw_return = float(item["expected_return"])
        corrected_return = raw_return * float(adjustment["return_scale"]) + float(
            adjustment["return_shift"]
        )
        corrected_return = _clip(corrected_return, -0.45, 0.65)

        raw_probability = float(item["up_probability"])
        corrected_probability = 0.5 + (raw_probability - 0.5) * float(
            adjustment["probability_scale"]
        )
        corrected_probability += float(adjustment["probability_shift"])
        corrected_probability = _clip(corrected_probability, 0.12, 0.88)

        old_target = float(item["target"])
        old_half_width = max(
            abs(float(item["high"]) - old_target),
            abs(old_target - float(item["low"])),
        )
        width_ratio = old_half_width / max(current, 1e-9)
        new_target = current * (1 + corrected_return)
        new_half_width = current * width_ratio * float(adjustment["interval_scale"])

        item["expected_return"] = corrected_return
        item["up_probability"] = corrected_probability
        item["target"] = new_target
        item["low"] = max(0.0, new_target - new_half_width)
        item["high"] = new_target + new_half_width
        item["signal"] = _signal(corrected_return, corrected_probability)
        item["learning"] = {
            "active": True,
            "samples": int(adjustment["samples"]),
            "return_shift": float(adjustment["return_shift"]),
            "return_scale": float(adjustment["return_scale"]),
            "probability_shift": float(adjustment["probability_shift"]),
            "probability_scale": float(adjustment["probability_scale"]),
            "interval_scale": float(adjustment["interval_scale"]),
        }
    result["learning_updated_at"] = hour_bucket().isoformat()
    return result


def _signal(expected_return: float, up_probability: float) -> str:
    if expected_return > 0.025 and up_probability >= 0.58:
        return "偏多"
    if expected_return < -0.025 and up_probability <= 0.42:
        return "偏空"
    return "震荡"


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
