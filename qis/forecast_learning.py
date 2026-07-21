from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math

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
        raw_model = _raw_model_values(item, current)
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

        raw_return = float(raw_model["expected_return"])
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

        raw_probability = float(raw_model["up_probability"])
        corrected_probability = 0.5 + (raw_probability - 0.5) * float(
            adjustment["probability_scale"]
        )
        corrected_probability += float(adjustment["probability_shift"])
        if raw_probability > 0.5:
            corrected_probability = max(0.5, corrected_probability)
        elif raw_probability < 0.5:
            corrected_probability = min(0.5, corrected_probability)
        corrected_probability = _clip(corrected_probability, 0.12, 0.88)

        width_ratio = float(raw_model["interval_half_width_ratio"])
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
        # Keep the pre-calibration model output so API refreshes can safely
        # reapply the latest per-instrument adjustment without shrinking an
        # already calibrated value for a second time.
        item["raw_model"] = raw_model
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
    data_quality = result.get("data_quality") or {}
    if data_quality and not bool(data_quality.get("actionable")):
        warnings = [str(item) for item in data_quality.get("warnings") or []]
        result["reference"] = {
            **result["reference"],
            "grade": "D",
            "state": "数据质量未通过",
            "reason": "；".join(warnings) or "历史K线质量未通过",
            "actionable": False,
        }
    grade = str(result["reference"]["grade"])
    result["opportunity_score"] = min(
        int(result["opportunity_score"]),
        {"A": 100, "B": 74, "C": 54, "D": 39}.get(grade, 39),
    )
    # Calibration must not turn a stale or gap-riddled history into an
    # apparently actionable short-term signal.  The raw engine applies the
    # same gate; repeat it here because this function recomputes the score.
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
    result["forecast_display"] = _forecast_display(result)
    result["learning_updated_at"] = hour_bucket().isoformat()
    return result


def _raw_model_values(item: dict, current: float) -> dict:
    stored = item.get("raw_model") or {}
    if {
        "expected_return",
        "up_probability",
        "interval_half_width_ratio",
    } <= set(stored):
        return {
            "expected_return": float(stored["expected_return"]),
            "up_probability": float(stored["up_probability"]),
            "interval_half_width_ratio": max(
                0.0,
                float(stored["interval_half_width_ratio"]),
            ),
        }

    corrected_return = float(item["expected_return"])
    corrected_probability = float(item["up_probability"])
    learning = item.get("learning") or {}
    if learning:
        return_scale = max(abs(float(learning.get("return_scale") or 0.0)), 1e-9)
        probability_scale = max(
            abs(float(learning.get("probability_scale") or 0.0)),
            1e-9,
        )
        raw_return = (
            corrected_return - float(learning.get("return_shift") or 0.0)
        ) / return_scale
        raw_probability = 0.5 + (
            corrected_probability
            - 0.5
            - float(learning.get("probability_shift") or 0.0)
        ) / probability_scale
        calibrated_target = float(item["target"])
        calibrated_half_width = max(
            abs(float(item["high"]) - calibrated_target),
            abs(calibrated_target - float(item["low"])),
        )
        interval_scale = max(
            abs(float(learning.get("interval_scale") or 0.0)),
            1e-9,
        )
        width_ratio = calibrated_half_width / max(current, 1e-9) / interval_scale
    else:
        raw_return = corrected_return
        raw_probability = corrected_probability
        raw_target = float(item["target"])
        raw_half_width = max(
            abs(float(item["high"]) - raw_target),
            abs(raw_target - float(item["low"])),
        )
        width_ratio = raw_half_width / max(current, 1e-9)
    return {
        "expected_return": raw_return,
        "up_probability": _clip(raw_probability, 0.0, 1.0),
        "interval_half_width_ratio": max(0.0, width_ratio),
    }


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
            test_windows=windows,
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
    baseline_accuracy = sum(baselines) / len(baselines)
    weakest_edge = min(edges)
    if weighted_accuracy < 0.50 or weakest_edge < 0:
        return _reference_payload(
            "D",
            "弱于基准",
            f"3天/7天样本外命中率 {weighted_accuracy * 100:.1f}%，未稳定超过基准",
            samples,
            False,
            test_windows=windows,
            direction_accuracy=weighted_accuracy,
            baseline_accuracy=baseline_accuracy,
            edge=weakest_edge,
        )

    directions = [float(item.get("expected_return") or 0.0) for item in core]
    probability = float(primary.get("up_probability") or 0.5)
    conviction = abs(probability - 0.5)
    agreement = directions[0] * directions[1] > 0
    directional_signals = [str(item.get("signal") or "震荡") for item in core]
    economically_meaningful = (
        directional_signals[0] in {"偏多", "偏空"}
        and directional_signals[0] == directional_signals[1]
    )
    if not agreement or conviction < 0.04 or not economically_meaningful:
        reason = (
            "校准后的3天/7天变动小于短线噪声阈值，暂无可执行优势"
            if agreement and conviction >= 0.04 and not economically_meaningful
            else "3天与7天方向不一致，或上涨概率过于接近中性"
        )
        return _reference_payload(
            "C",
            "等待确认",
            reason,
            samples,
            False,
            test_windows=windows,
            direction_accuracy=weighted_accuracy,
            baseline_accuracy=baseline_accuracy,
            edge=weakest_edge,
        )

    if weighted_accuracy >= 0.56 and weakest_edge >= 0.025 and conviction >= 0.08:
        return _reference_payload(
            "A",
            "可参考",
            f"样本外命中率 {weighted_accuracy * 100:.1f}%，3天与7天方向一致",
            samples,
            True,
            test_windows=windows,
            direction_accuracy=weighted_accuracy,
            baseline_accuracy=baseline_accuracy,
            edge=weakest_edge,
        )
    return _reference_payload(
        "B",
        "谨慎参考",
        f"样本外命中率 {weighted_accuracy * 100:.1f}%，已过基准但优势有限",
        samples,
        True,
        test_windows=windows,
        direction_accuracy=weighted_accuracy,
        baseline_accuracy=baseline_accuracy,
        edge=weakest_edge,
    )


def _reference_payload(
    grade: str,
    state: str,
    reason: str,
    samples: int,
    actionable: bool,
    *,
    test_windows: int = 0,
    direction_accuracy: float | None = None,
    baseline_accuracy: float | None = None,
    edge: float | None = None,
) -> dict:
    return {
        "grade": grade,
        "state": state,
        "reason": reason,
        "samples": samples,
        "test_windows": test_windows,
        "direction_accuracy": direction_accuracy,
        "baseline_accuracy": baseline_accuracy,
        "edge": edge,
        "actionable": actionable,
        "primary_horizon": "3d",
        "confirmation_horizon": "1w",
    }


def _forecast_display(forecast: dict) -> dict:
    """Build an honest presentation payload without discarding audit values.

    Unvalidated directional returns remain stored for future evaluation, but
    consumers are told to show a volatility envelope instead of presenting a
    heavily shrunken number as a useful point forecast.
    """
    reference = forecast.get("reference") or {}
    actionable = bool(reference.get("actionable"))
    current = max(float(forecast.get("current_price") or 0.0), 0.0)
    daily_volatility = max(float(forecast.get("volatility") or 0.0), 0.0)
    envelopes = []
    for item in forecast.get("forecasts") or []:
        days = max(1, int(item.get("days") or 1))
        sigma = min(0.50, daily_volatility * math.sqrt(days))
        envelopes.append({
            "key": str(item.get("key") or ""),
            "days": days,
            "expected_move": math.expm1(sigma),
            "low": current * math.exp(-sigma),
            "high": current * math.exp(sigma),
        })
    return {
        "mode": "directional_forecast" if actionable else "volatility_envelope",
        "direction_available": actionable,
        "reason": str(reference.get("reason") or ""),
        "envelope_basis": "realized_volatility_1sigma",
        "envelopes": envelopes,
    }


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
