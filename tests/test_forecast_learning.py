import pytest

from qis.forecast_learning import apply_strategy_adjustments


def test_strategy_learning_applies_bounded_calibration() -> None:
    forecast = {
        "current_price": 100.0,
        "forecasts": [{
            "key": "1w",
            "target": 110.0,
            "low": 90.0,
            "high": 120.0,
            "expected_return": 0.10,
            "up_probability": 0.70,
            "signal": "偏多",
        }],
    }
    adjustments = {
        "1w": {
            "active": True,
            "samples": 100,
            "return_shift": -0.02,
            "return_scale": 0.80,
            "probability_shift": -0.05,
            "probability_scale": 0.80,
            "interval_scale": 1.20,
        }
    }

    result = apply_strategy_adjustments(forecast, adjustments)
    item = result["forecasts"][0]

    assert item["expected_return"] == pytest.approx(0.06)
    assert item["up_probability"] == pytest.approx(0.61)
    assert item["target"] == pytest.approx(106.0)
    assert item["learning"]["active"] is True


def test_strategy_learning_can_strongly_neutralize_unreliable_horizon() -> None:
    forecast = {
        "current_price": 100.0,
        "forecasts": [{
            "key": "1w",
            "target": 120.0,
            "low": 90.0,
            "high": 130.0,
            "expected_return": 0.20,
            "up_probability": 0.80,
            "signal": "偏多",
        }],
    }
    adjustments = {
        "1w": {
            "active": True,
            "samples": 800,
            "return_shift": 0.0,
            "return_scale": 0.20,
            "probability_shift": 0.0,
            "probability_scale": 0.20,
            "interval_scale": 1.0,
        }
    }

    item = apply_strategy_adjustments(forecast, adjustments)["forecasts"][0]

    assert item["expected_return"] == pytest.approx(0.04)
    assert item["up_probability"] == pytest.approx(0.56)
    assert item["signal"] == "偏多"


def test_reapplying_calibration_does_not_shrink_forecast_twice() -> None:
    forecast = {
        "current_price": 100.0,
        "forecasts": [{
            "key": "1w",
            "target": 110.0,
            "low": 90.0,
            "high": 120.0,
            "expected_return": 0.10,
            "up_probability": 0.70,
            "signal": "偏多",
        }],
    }
    adjustments = {
        "1w": {
            "active": True,
            "samples": 100,
            "return_shift": 0.0,
            "return_scale": 0.20,
            "probability_shift": 0.0,
            "probability_scale": 0.20,
            "interval_scale": 1.10,
        }
    }

    first = apply_strategy_adjustments(forecast, adjustments)
    second = apply_strategy_adjustments(first, adjustments)

    assert first["forecasts"][0]["expected_return"] == pytest.approx(0.02)
    assert second["forecasts"][0]["expected_return"] == pytest.approx(0.02)
    assert second["forecasts"][0]["up_probability"] == pytest.approx(0.54)
    assert second["forecasts"][0]["target"] == pytest.approx(
        first["forecasts"][0]["target"]
    )


def test_market_wide_calibration_cannot_reverse_asset_direction() -> None:
    forecast = {
        "current_price": 100.0,
        "forecasts": [{
            "key": "6m",
            "target": 145.0,
            "low": 80.0,
            "high": 170.0,
            "expected_return": 0.45,
            "up_probability": 0.66,
            "signal": "偏多",
        }],
    }
    adjustments = {
        "6m": {
            "active": True,
            "samples": 150,
            "return_shift": -0.08,
            "return_scale": 0.12,
            "probability_shift": -0.15,
            "probability_scale": 1.05,
            "interval_scale": 1.0,
        }
    }

    item = apply_strategy_adjustments(forecast, adjustments)["forecasts"][0]

    assert item["expected_return"] == 0.0
    assert item["up_probability"] >= 0.5
    assert item["signal"] == "震荡"


def test_unvalidated_model_uses_conservative_cold_start_prior() -> None:
    forecast = {
        "current_price": 100.0,
        "forecasts": [{
            "key": "6m",
            "target": 145.0,
            "low": 80.0,
            "high": 170.0,
            "expected_return": 0.45,
            "up_probability": 0.66,
            "signal": "偏多",
        }],
    }

    item = apply_strategy_adjustments(
        forecast,
        {"6m": {"active": False, "samples": 0}},
    )["forecasts"][0]

    assert item["expected_return"] == pytest.approx(0.2925)
    assert item["up_probability"] == pytest.approx(0.596)
    assert item["learning"]["active"] is False
    assert item["learning"]["calibration_method"] == "cold_start_conservative_prior"


def test_new_strategy_variant_does_not_borrow_adaptive_calibration() -> None:
    variant = {
        "strategy": {"id": "trend"},
        "current_price": 100.0,
        "volatility": 0.02,
        "market_context": {},
        "forecasts": [{
            "key": "1m",
            "target": 120.0,
            "low": 80.0,
            "high": 140.0,
            "expected_return": 0.20,
            "up_probability": 0.70,
            "confidence": 0.65,
            "signal": "偏多",
        }],
    }

    result = apply_strategy_adjustments(variant, {})
    item = result["forecasts"][0]

    assert item["expected_return"] == pytest.approx(0.13)
    assert item["learning"]["calibration_method"] == "cold_start_conservative_prior"
    assert result["decision"] == "模拟观察"
    assert result["strategy_validation"] == "冷启动待验证"


def test_data_quality_gate_overrides_strategy_cold_start_label() -> None:
    variant = {
        "strategy": {"id": "trend"},
        "current_price": 100.0,
        "volatility": 0.02,
        "market_context": {},
        "data_quality": {
            "quality": "D",
            "score": 35,
            "actionable": False,
            "bars": 40,
            "warnings": ["历史K线不足90根"],
        },
        "forecasts": [
            {
                "key": key,
                "target": 102.0,
                "low": 95.0,
                "high": 108.0,
                "expected_return": 0.02,
                "up_probability": 0.56,
                "confidence": 0.45,
                "signal": "偏多",
            }
            for key in ("1d", "3d", "1w", "2w")
        ],
    }

    adjustments = {
        key: {
            "active": True,
            "samples": 80,
            "test_windows": 20,
            "return_shift": 0.0,
            "return_scale": 2.0,
            "probability_shift": 0.0,
            "probability_scale": 1.0,
            "interval_scale": 1.0,
            "direction_accuracy": 0.65,
            "baseline_accuracy": 0.50,
            "edge": 0.15,
        }
        for key in ("1d", "3d", "1w", "2w")
    }

    result = apply_strategy_adjustments(variant, adjustments)

    assert result["decision"] == "历史数据质量不足，观望"
    assert result["strategy_validation"] == "数据质量未通过"
    assert result["opportunity_score"] <= 39
    assert result["reference"]["grade"] == "D"
    assert result["reference"]["actionable"] is False
    assert result["forecast_display"]["mode"] == "volatility_envelope"
    assert result["forecast_display"]["direction_available"] is False


def test_small_calibrated_moves_are_presented_as_volatility_not_direction() -> None:
    variant = {
        "current_price": 100.0,
        "volatility": 0.02,
        "market_context": {},
        "forecasts": [
            {
                "key": key,
                "days": days,
                "target": 100.0 * (1 + expected_return),
                "low": 94.0,
                "high": 106.0,
                "expected_return": expected_return,
                "up_probability": 0.65,
                "confidence": 0.60,
                "signal": "偏多",
            }
            for key, days, expected_return in (
                ("1d", 1, 0.004),
                ("3d", 3, 0.008),
                ("1w", 7, 0.015),
                ("2w", 14, 0.020),
            )
        ],
    }
    adjustments = {
        key: {
            "active": True,
            "samples": 80,
            "test_windows": 20,
            "return_shift": 0.0,
            "return_scale": 1.0,
            "probability_shift": 0.0,
            "probability_scale": 1.0,
            "interval_scale": 1.0,
            "direction_accuracy": 0.62,
            "baseline_accuracy": 0.52,
            "edge": 0.10,
        }
        for key in ("1d", "3d", "1w", "2w")
    }

    result = apply_strategy_adjustments(variant, adjustments)
    display = result["forecast_display"]
    three_day = next(item for item in display["envelopes"] if item["key"] == "3d")

    assert result["reference"]["actionable"] is False
    assert "噪声阈值" in result["reference"]["reason"]
    assert display["mode"] == "volatility_envelope"
    assert three_day["low"] < 100.0 < three_day["high"]
    assert three_day["expected_move"] == pytest.approx(0.0353, abs=0.0002)
