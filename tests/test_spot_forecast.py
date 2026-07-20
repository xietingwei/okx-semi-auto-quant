from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

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


def _rebound_candidate_candles(count: int = 260) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = 100.0
    for index in range(count):
        if index < count - 36:
            price *= 1.0025
        elif index < count - 24:
            price *= 0.975
        else:
            price *= 1.006
        volume = 1000 + index * 2
        if index >= count - 36:
            volume *= 1.45
        candles.append(
            Candle(
                ts=start + timedelta(days=index),
                open=price * 0.992,
                high=price * 1.018,
                low=price * 0.982,
                close=price,
                volume=volume,
            )
        )
    return candles


def _breakdown_candles(count: int = 260) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = 160.0
    for index in range(count):
        price *= 0.997 if index < count - 30 else 0.988
        candles.append(
            Candle(
                ts=start + timedelta(days=index),
                open=price * 1.012,
                high=price * 1.018,
                low=price * 0.978,
                close=price,
                volume=1400 + index,
            )
        )
    return candles


def test_spot_forecast_has_all_horizons() -> None:
    forecast = SpotForecastEngine().analyze("BTC-USDT", _daily_candles())

    assert forecast is not None
    assert [item.key for item in forecast.forecasts] == ["1d", "1w", "1m", "3m", "6m"]
    assert all(item.low <= item.target <= item.high for item in forecast.forecasts)
    assert max(abs(item.expected_return) for item in forecast.forecasts) <= 0.45


def test_spot_forecast_preserves_six_month_history_for_deep_analysis() -> None:
    candles = _daily_candles(count=240)
    forecast = SpotForecastEngine().analyze("BTC-USDT", candles)

    assert forecast is not None
    assert len(forecast.history) >= 181
    assert forecast.history[-1]["date"] == candles[-2].ts.isoformat()


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


def test_rebound_candidate_scores_recent_pullback_with_support() -> None:
    forecast = SpotForecastEngine().analyze(
        "BTC-USDT",
        _rebound_candidate_candles(),
        market_context={
            "volume_score": 0.65,
            "market_environment_score": 0.12,
            "market_environment_label": "过渡震荡",
        },
    )

    assert forecast is not None
    assert forecast.rebound_score >= 65
    assert forecast.decision == "跌后反弹候选"
    assert forecast.factors["rebound"] == "强反弹候选"
    assert "高点折价" in forecast.factors["discount"]


def test_rebound_score_rejects_breakdown_as_bottom_fishing() -> None:
    forecast = SpotForecastEngine().analyze(
        "BTC-USDT",
        _breakdown_candles(),
        market_context={
            "volume_score": -0.45,
            "market_environment_score": -0.35,
            "market_environment_label": "风险收缩",
        },
    )

    assert forecast is not None
    assert forecast.rebound_score <= 35
    assert forecast.decision == "破位风险，暂不抄底"
    assert forecast.factors["rebound"] == "破位风险"


def test_low_rebound_score_does_not_mark_clean_uptrend_as_breakdown() -> None:
    forecast = SpotForecastEngine().analyze("BTC-USDT", _daily_candles())

    assert forecast is not None
    assert forecast.rebound_score <= 40
    assert forecast.decision != "破位风险，暂不抄底"
    assert forecast.factors["rebound"] != "破位风险"


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
    assert "direction:'方向'" in html
    assert "upProbability:'上涨概率'" in html
    assert "downProbability:'下跌概率'" in html
    assert "futureTrend:'未来走势'" in html
    assert "tradingVolume:'成交量'" in html
    assert 'id="volumeRatio"' in html
    assert 'id="decisionChart"' in html
    assert "function drawDecisionChart" in html
    assert 'id="chartStats"' in html
    assert "function loadRangeCandles(forecast)" in html
    assert 'id="chartRanges"' in html
    assert 'data-range="1D"' in html
    assert 'data-range="6M"' in html
    assert 'data-range="ALL"' in html
    assert "function movingAverage" in html
    assert "function calculateRsi" in html
    assert "function calculateSar" in html
    assert "function calculateStochRsi" in html
    assert "'&range='+encodeURIComponent(requestedRange)" in html
    assert 'data-main-indicator="ICHIMOKU"' in html
    assert '<option value="MACD" selected>' in html
    assert '<option value="OBV">OBV</option>' in html
    assert "exitLevels:'卖出价格'" in html
    assert 'data-frame="1H"' not in html
    assert 'id="chartTooltip"' in html
    assert "function bindChartInteractions(points,dims)" in html
    assert 'id="hoverLayer"' in html
    assert 'id="deepAnalysisBtn"' in html
    assert 'id="deepDialog"' in html
    assert "/api/deep-analysis?inst_id=" in html
    assert "function renderDeepAnalysis(analysis)" in html
    assert "coreHitRate:'核心命中率'" in html
    assert "notQualified:'未达标'" in html
    assert "quality.core_validation_rate" in html
    assert 'id="deepRankBtn"' in html
    assert 'id="deepRankDialog"' in html
    assert "'/api/deep-analysis?inst_id='+encodeURIComponent(asset.inst_id)+'&days=180'" in html
    assert "/api/deep-analysis/rank?days=180" in html
    assert "function renderDeepRank" in html
    assert "deepRank:'深度分析排名'" in html
    assert "data-detail-inst" in html
    assert "openPositionDetail(row.dataset.detailInst)" in html
    assert 'data-view="usStocks"' in html
    assert "usStockOpportunities:'美股机会'" in html
    assert "location.hash=name==='usStocks'?'us-stocks':'opportunity'" in html
    assert "radarScope==='usStocks'?'equity':$('radarMarket').value" in html
    assert "exchange=x.exchange&&!String(source).includes(x.exchange)?x.exchange:''" in html
    assert "美股现货':'US Stock" in html
    assert "reboundPotential:'反弹潜力'" in html
    assert "factorRebound:'反弹结构'" in html
    assert "sort==='rebound'" in html
    assert "/api/spot/delete" in html
    assert "deletePosition" in html
    assert "deleteConfirm" in html
    assert "assistantTopButton" not in html
    assert "spirit-button" not in html
    assert "/api/assistant/" not in html
    assert "模型诊断" not in html
    assert "神经网络影子大脑" not in html
    assert "选择策略" not in html


def test_start_script_preloads_latest_dashboard_before_services() -> None:
    script = Path("scripts/start.sh").read_text(encoding="utf-8")

    assert "预加载最新行情与仪表盘" in script
    assert "QIS_PRELOAD_TIMEOUT_SECONDS" in script
    assert "timeout=timeout_seconds" in script
    assert '"-m", "qis", "spot-dashboard", "--out"' in script
    assert script.index("spot-dashboard") < script.index("启动现货预测刷新")
