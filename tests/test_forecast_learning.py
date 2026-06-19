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
    assert item["signal"] == "震荡"
