from datetime import datetime, timedelta, timezone
import json

from qis.models import Candle
from qis.spot_dashboard import render_spot_dashboard_cache
from qis.spot_forecast import SpotForecastEngine


def _daily_candles(count: int = 320) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = 100.0
    for index in range(count):
        price *= 1.001
        candles.append(
            Candle(
                ts=start + timedelta(days=index),
                open=price * 0.995,
                high=price * 1.012,
                low=price * 0.988,
                close=price,
                volume=1000 + index,
            )
        )
    return candles


def test_spot_forecast_has_all_horizons() -> None:
    forecast = SpotForecastEngine().analyze("BTC-USDT", _daily_candles())

    assert forecast is not None
    assert [item.key for item in forecast.forecasts] == ["1d", "1w", "1m", "3m", "6m"]
    assert all(item.low <= item.target <= item.high for item in forecast.forecasts)


def test_spot_forecast_marks_equity_mapping() -> None:
    forecast = SpotForecastEngine().analyze("NVDA-USDT-SWAP", _daily_candles())

    assert forecast is not None
    assert forecast.market_type == "股票映射行情"


def test_spot_forecast_uses_live_price_without_polluting_closed_history() -> None:
    quote_time = datetime(2026, 6, 18, 3, 20, tzinfo=timezone.utc)
    forecast = SpotForecastEngine().analyze(
        "BTC-USDT",
        _daily_candles(),
        live_price=150.0,
        quote_time=quote_time,
    )

    assert forecast is not None
    assert forecast.current_price == 150.0
    assert forecast.quote_time == quote_time.isoformat()
    assert forecast.quote_source == "OKX ticker"
    assert {"open", "high", "low", "close", "volume"} <= set(
        forecast.history[-1]
    )


def test_cached_forecasts_rebuild_latest_dashboard_template(tmp_path) -> None:
    cache = tmp_path / "spot_forecasts.json"
    output = tmp_path / "index.html"
    cache.write_text(
        json.dumps([{"inst_id": "BTC-USDT"}]),
        encoding="utf-8",
    )

    rendered = render_spot_dashboard_cache(cache, output)

    assert rendered == output
    html = output.read_text(encoding="utf-8")
    assert "assistantTopButton" in html
    assert "卖出价格纪律" in html
    assert ".message.ai{" in html
    assert "role==='assistant'?'ai':role" in html
    assert ".message.assistant{" not in html
    assert 'data-frame="1H"' in html
    assert 'data-scope="global"' in html
    assert "/api/assistant/stream" in html
