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
