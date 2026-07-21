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

    result = apply_strategy_adjustments(variant, {})

    assert result["decision"] == "历史数据质量不足，观望"
    assert result["strategy_validation"] == "数据质量未通过"
    assert result["opportunity_score"] <= 39
