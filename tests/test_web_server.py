import pytest
from datetime import datetime, timedelta, timezone

from qis.web_server import _rebase_forecast, _rebase_forecast_prices


def test_rebase_forecast_uses_latest_price_for_all_price_targets() -> None:
    forecast = {
        "inst_id": "BTC-USDT",
        "current_price": 100.0,
        "quote_time": "2026-06-18T03:00:00+00:00",
        "daily_change": 0.0,
        "buy_zone_low": 95.0,
        "buy_zone_high": 101.0,
        "invalidation": 90.0,
        "forecasts": [{
            "key": "1w",
            "expected_return": 0.10,
            "target": 110.0,
            "low": 90.0,
            "high": 120.0,
        }],
    }

    result = _rebase_forecast(
        forecast,
        {"last": "200", "open24h": "190", "ts": "1781752800000"},
    )

    assert result["current_price"] == 200.0
    assert result["forecasts"][0]["target"] == pytest.approx(220.0)
    assert result["forecasts"][0]["low"] == pytest.approx(180.0)
    assert result["forecasts"][0]["high"] == pytest.approx(240.0)
    assert result["buy_zone_low"] == pytest.approx(190.0)
    assert result["invalidation"] == pytest.approx(180.0)


def test_rebase_forecast_recalculates_signal_features_from_live_price() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    history = [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "open": 100 + index * 0.1,
            "high": 101 + index * 0.1,
            "low": 99 + index * 0.1,
            "close": 100 + index * 0.1,
            "volume": 1000,
        }
        for index in range(120)
    ]
    forecast = {
        "inst_id": "BTC-USDT",
        "current_price": history[-1]["close"],
        "history": history,
    }

    lower = _rebase_forecast(
        forecast,
        {"last": "112", "ts": "1781752800000"},
    )
    higher = _rebase_forecast(
        forecast,
        {"last": "125", "ts": "1781752800000"},
    )

    assert lower["current_price"] == 112
    assert higher["current_price"] == 125
    assert higher["forecast_base_price"] == 125
    assert higher["quote_source"] == "OKX ticker · 实时特征重算"
    assert higher["forecasts"][0]["expected_return"] != pytest.approx(
        lower["forecasts"][0]["expected_return"]
    )


def test_price_rebase_updates_targets_and_strategy_variants_without_model_run() -> None:
    forecast = {
        "inst_id": "BTC-USDT",
        "current_price": 100.0,
        "buy_zone_low": 95.0,
        "buy_zone_high": 101.0,
        "invalidation": 90.0,
        "forecasts": [
            {
                "key": "1w",
                "expected_return": 0.10,
                "target": 110.0,
                "low": 90.0,
                "high": 120.0,
            }
        ],
        "strategy_variants": [
            {
                "strategy": {"id": "trend"},
                "model_version": "model:trend",
                "current_price": 100.0,
                "forecasts": [
                    {
                        "key": "1w",
                        "expected_return": 0.05,
                        "target": 105.0,
                        "low": 92.0,
                        "high": 115.0,
                    }
                ],
            }
        ],
    }

    result = _rebase_forecast_prices(
        forecast,
        {"last": "200", "open24h": "190", "ts": "1781752800000"},
    )

    assert result["current_price"] == 200.0
    assert result["quote_source"] == "OKX ticker · 5秒动态基准"
    assert result["buy_zone_low"] == pytest.approx(190.0)
    assert result["forecasts"][0]["target"] == pytest.approx(220.0)
    assert result["forecasts"][0]["low"] == pytest.approx(180.0)
    assert result["strategy_variants"][0]["current_price"] == 200.0
    assert result["strategy_variants"][0]["forecasts"][0]["target"] == pytest.approx(210.0)
    assert forecast["current_price"] == 100.0
