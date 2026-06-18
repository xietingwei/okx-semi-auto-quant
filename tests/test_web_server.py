import pytest

from qis.web_server import _rebase_forecast


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
