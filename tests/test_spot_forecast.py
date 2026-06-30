from datetime import datetime, timedelta, timezone
import json

import pytest

from qis.models import Candle
from qis.spot_dashboard import render_spot_dashboard_cache
from qis.spot_forecast import STRATEGY_CATALOG, SpotForecastEngine, decide_strategy


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
    assert max(abs(item.expected_return) for item in forecast.forecasts) <= 0.45


def test_long_horizon_momentum_is_not_linearly_amplified() -> None:
    value_90 = SpotForecastEngine._momentum_blend(90, 0.1, 0.2, 0.3)
    value_180 = SpotForecastEngine._momentum_blend(180, 0.1, 0.2, 0.3)

    assert value_180 == pytest.approx(0.28)
    assert value_180 <= value_90 * 1.2


def test_expected_return_soft_bound_preserves_extreme_ranking() -> None:
    moderate = SpotForecastEngine._soft_bound(0.60, 0.35, 0.45)
    strong = SpotForecastEngine._soft_bound(1.20, 0.35, 0.45)

    assert 0 < moderate < strong < 0.45
    assert SpotForecastEngine._soft_bound(-0.80, 0.35, 0.45) > -0.35


def test_spot_forecast_marks_equity_mapping() -> None:
    forecast = SpotForecastEngine().analyze("NVDA-USDT-SWAP", _daily_candles())

    assert forecast is not None
    assert forecast.market_type == "股票映射行情"


def test_risk_contraction_blocks_normal_buy_decision() -> None:
    forecast = SpotForecastEngine().analyze(
        "BTC-USDT",
        _daily_candles(),
        live_price=140.0,
        market_context={
            "market_environment_score": -0.8,
            "market_environment_label": "风险收缩",
        },
    )

    assert forecast is not None
    assert forecast.decision != "分批关注买入"
    assert 0 <= forecast.opportunity_score <= 100


def test_strategy_never_recommends_buy_below_70_score() -> None:
    forecasts = [
        {
            "key": key,
            "expected_return": 0.08,
            "up_probability": 0.65,
            "confidence": 0.65,
        }
        for key in ("1w", "1m", "3m")
    ]

    assert decide_strategy(forecasts, 50, 0.2) == "中性观察"
    assert decide_strategy(forecasts, 69, 0.2) == "观察等待触发"
    assert decide_strategy(forecasts, 70, 0.2) == "分批关注买入"


def test_strategy_suite_has_distinct_models_and_documented_focus() -> None:
    suite = SpotForecastEngine().analyze_suite(
        "BTC-USDT",
        _daily_candles(),
        live_price=150.0,
        market_context={
            "orderbook_score": 0.5,
            "volume_score": 0.7,
            "market_environment_score": 0.2,
        },
    )

    assert [item["strategy"]["id"] for item in suite] == [
        item["id"] for item in STRATEGY_CATALOG
    ]
    assert all(item["strategy"]["direction"] for item in suite)
    one_month_returns = {
        round(
            next(
                row for row in item["forecasts"] if row["key"] == "1m"
            )["expected_return"],
            8,
        )
        for item in suite
    }
    assert len(one_month_returns) >= 3


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
    baseline = SpotForecastEngine().analyze("BTC-USDT", _daily_candles())
    assert baseline is not None
    assert forecast.forecasts[0].expected_return != pytest.approx(
        baseline.forecasts[0].expected_return
    )
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
    assert "exitLevels:'卖出价格'" in html
    assert "strategyNames[selectedRadarStrategy]" not in html
    assert ".message.ai{" in html
    assert "role==='assistant'?'ai':role" in html
    assert ".message.assistant{" not in html
    assert 'data-frame="1H"' in html
    assert 'data-view="usStocks"' in html
    assert "messages.zh.usStockOpportunities='美股机会'" in html
    assert "location.hash=radarView==='usStocks'?'us-stocks':'opportunity'" in html
    assert "radarScope==='usStocks'?'equity':$('radarMarket').value" in html
    assert "sourceLine=x=>[x.data_source||x.quote_source,x.trade_platform]" in html
    assert "美股现货':'US Stock" in html
    assert 'data-scope="global"' in html
    assert 'data-radar-strategy="adaptive"' in html
    assert 'data-radar-strategy="trend"' in html
    assert 'data-radar-strategy="breakout"' in html
    assert 'data-radar-strategy="mean_reversion"' in html
    assert "strategyView(x,selectedRadarStrategy)" in html
    assert "<small>${x.strategy?.name||strategyNames[selectedRadarStrategy]}</small>" not in html
    assert "/api/assistant/stream" in html
    assert "/api/spot/delete" in html
    assert "deletePosition" in html
    assert "deleteConfirm" in html
