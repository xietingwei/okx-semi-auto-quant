from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from qis.spot_forecast import decide_strategy, score_opportunity

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
        learning_active = bool(adjustment.get("active"))
        if not learning_active:
            adjustment = {
                "samples": int(adjustment.get("samples", 0)),
                "return_shift": 0.0,
                "return_scale": 0.65,
                "probability_shift": 0.0,
                "probability_scale": 0.60,
                "interval_scale": 1.15,
                "calibration_method": "cold_start_conservative_prior",
            }

        raw_return = float(item["expected_return"])
        corrected_return = raw_return * float(adjustment["return_scale"]) + float(
            adjustment["return_shift"]
        )
        if raw_return > 0:
            corrected_return = max(0.0, corrected_return)
        elif raw_return < 0:
            corrected_return = min(0.0, corrected_return)
        horizon_limit = {
            "1d": 0.08,
            "3d": 0.14,
            "1w": 0.22,
            "2w": 0.30,
        }.get(str(item["key"]), 0.30)
        corrected_return = _clip(corrected_return, -horizon_limit, horizon_limit)

        raw_probability = float(item["up_probability"])
        corrected_probability = 0.5 + (raw_probability - 0.5) * float(
            adjustment["probability_scale"]
        )
        corrected_probability += float(adjustment["probability_shift"])
        if raw_probability > 0.5:
            corrected_probability = max(0.5, corrected_probability)
        elif raw_probability < 0.5:
            corrected_probability = min(0.5, corrected_probability)
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
        item["signal"] = _signal(
            str(item["key"]),
            corrected_return,
            corrected_probability,
        )
        item["learning"] = {
            "active": learning_active,
            "samples": int(adjustment["samples"]),
            "return_shift": float(adjustment["return_shift"]),
            "return_scale": float(adjustment["return_scale"]),
            "probability_shift": float(adjustment["probability_shift"]),
            "probability_scale": float(adjustment["probability_scale"]),
            "interval_scale": float(adjustment["interval_scale"]),
            "test_windows": int(adjustment.get("test_windows", 0)),
            "direction_accuracy": adjustment.get("direction_accuracy"),
            "baseline_accuracy": adjustment.get("baseline_accuracy"),
            "edge": adjustment.get("edge"),
            "calibration_method": adjustment.get("calibration_method", "bounded"),
        }
    result["opportunity_score"] = score_opportunity(
        result["forecasts"],
        float(result.get("volatility", 0.0)),
    )
    result["decision"] = decide_strategy(
        result["forecasts"],
        result["opportunity_score"],
        float(result.get("market_context", {}).get("market_environment_score", 0.0)),
    )
    result["reference"] = _reference_gate(result["forecasts"])
    grade = str(result["reference"]["grade"])
    result["opportunity_score"] = min(
        int(result["opportunity_score"]),
        {"A": 100, "B": 74, "C": 54, "D": 39}.get(grade, 39),
    )
    # Calibration must not turn a stale or gap-riddled history into an
    # apparently actionable short-term signal.  The raw engine applies the
    # same gate; repeat it here because this function recomputes the score.
    data_quality = result.get("data_quality") or {}
    if data_quality and not bool(data_quality.get("actionable")):
        result["opportunity_score"] = min(int(result["opportunity_score"]), 39)
    if grade == "D":
        result["decision"] = "历史验证不足，观望"
    elif grade == "C":
        result["decision"] = "短线信号冲突，观望"
    elif grade == "B" and result["decision"] == "短线条件成立":
        result["decision"] = "短线条件成立（谨慎）"
    if data_quality and not bool(data_quality.get("actionable")):
        result["decision"] = "历史数据质量不足，观望"
    strategy_id = result.get("strategy", {}).get("id", "adaptive")
    if strategy_id != "adaptive" and not result["reference"]["actionable"]:
        result["decision"] = (
            "历史数据质量不足，观望"
            if data_quality and not bool(data_quality.get("actionable"))
            else "模拟观察"
        )
        result["strategy_validation"] = (
            "数据质量未通过"
            if data_quality and not bool(data_quality.get("actionable"))
            else "冷启动待验证"
        )
    else:
        result["strategy_validation"] = result["reference"]["state"]
    result["learning_updated_at"] = hour_bucket().isoformat()
    return result


def _signal(horizon: str, expected_return: float, up_probability: float) -> str:
    threshold = {"1d": 0.006, "3d": 0.012, "1w": 0.020, "2w": 0.030}.get(
        horizon,
        0.02,
    )
    if expected_return > threshold and up_probability >= 0.55:
        return "偏多"
    if expected_return < -threshold and up_probability <= 0.45:
        return "偏空"
    return "震荡"


def _reference_gate(forecasts: list[dict]) -> dict:
    by_key = {str(item.get("key")): item for item in forecasts}
    primary = by_key.get("3d")
    confirmation = by_key.get("1w")
    if primary is None or confirmation is None:
        return _reference_payload("D", "仅观察", "缺少3天或7天核心预测", 0, False)

    core = (primary, confirmation)
    learning = [item.get("learning", {}) for item in core]
    samples = min(int(item.get("samples", 0)) for item in learning)
    windows = min(int(item.get("test_windows", 0)) for item in learning)
    if not all(bool(item.get("active")) for item in learning) or windows < 8:
        return _reference_payload(
            "D",
            "仅观察",
            f"独立验证窗口不足（{windows}），暂不作为交易依据",
            samples,
            False,
        )

    accuracies = [float(item.get("direction_accuracy") or 0.0) for item in learning]
    baselines = [float(item.get("baseline_accuracy") or 0.5) for item in learning]
    edges = [
        float(item.get("edge"))
        if item.get("edge") is not None
        else accuracy - baseline
        for item, accuracy, baseline in zip(learning, accuracies, baselines)
    ]
    weighted_accuracy = sum(accuracies) / len(accuracies)
    weakest_edge = min(edges)
    if weighted_accuracy < 0.50 or weakest_edge < 0:
        return _reference_payload(
            "D",
            "弱于基准",
            f"3天/7天样本外命中率 {weighted_accuracy * 100:.1f}%，未稳定超过基准",
            samples,
            False,
        )

    directions = [float(item.get("expected_return") or 0.0) for item in core]
    probability = float(primary.get("up_probability") or 0.5)
    conviction = abs(probability - 0.5)
    agreement = directions[0] * directions[1] > 0
    if not agreement or conviction < 0.04:
        return _reference_payload(
            "C",
            "等待确认",
            "3天与7天方向不一致，或上涨概率过于接近中性",
            samples,
            False,
        )

    if weighted_accuracy >= 0.56 and weakest_edge >= 0.025 and conviction >= 0.08:
        return _reference_payload(
            "A",
            "可参考",
            f"样本外命中率 {weighted_accuracy * 100:.1f}%，3天与7天方向一致",
            samples,
            True,
        )
    return _reference_payload(
        "B",
        "谨慎参考",
        f"样本外命中率 {weighted_accuracy * 100:.1f}%，已过基准但优势有限",
        samples,
        True,
    )


def _reference_payload(
    grade: str,
    state: str,
    reason: str,
    samples: int,
    actionable: bool,
) -> dict:
    return {
        "grade": grade,
        "state": state,
        "reason": reason,
        "samples": samples,
        "actionable": actionable,
        "primary_horizon": "3d",
        "confirmation_horizon": "1w",
    }


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
