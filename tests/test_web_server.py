import pytest
from datetime import datetime, timedelta, timezone

from qis.models import Candle
from qis.web_server import (
    CANDLE_RANGE_SPECS,
    QisRequestHandler,
    _rebase_forecast,
    _rebase_forecast_prices,
)


def _rank_route_forecast() -> dict:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    history = []
    for index in range(120):
        open_price = price
        price *= 1.004
        history.append(
            {
                "date": (start + timedelta(days=index)).date().isoformat(),
                "open": open_price,
                "high": price * 1.006,
                "low": open_price * 0.994,
                "close": price,
                "volume": 1000 + index,
            }
        )
    return {
        "inst_id": "RANK-USDT",
        "symbol": "RANK",
        "market_type": "现货",
        "current_price": price,
        "history": history,
        "forecasts": [{"key": "1w", "expected_return": 0.03}],
    }


def _route_forecast(
    inst_id: str,
    *,
    symbol: str,
    count: int,
    source: str,
    market_type: str,
) -> dict:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    history = []
    for index in range(count):
        open_price = price
        price *= 1.002
        history.append(
            {
                "date": (start + timedelta(days=index)).date().isoformat(),
                "open": open_price,
                "high": price * 1.006,
                "low": open_price * 0.994,
                "close": price,
                "volume": 1000 + index,
            }
        )
    return {
        "inst_id": inst_id,
        "symbol": symbol,
        "market_type": market_type,
        "data_source": source,
        "quote_source": source,
        "current_price": price,
        "history": history,
        "forecasts": [{"key": "1w", "expected_return": 0.03}],
    }


def test_positions_route_returns_only_decision_payload(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/spot/positions"
    payloads = []

    class StubStorage:
        @staticmethod
        def spot_positions():
            return []

    handler.storage = StubStorage()
    monkeypatch.setattr(QisRequestHandler, "_position_forecasts", lambda self, rows: {})
    monkeypatch.setattr(QisRequestHandler, "_position_analyses", lambda self, rows, forecasts: [])
    monkeypatch.setattr(
        QisRequestHandler,
        "_json",
        lambda self, payload, status=200: payloads.append((status, payload)),
    )

    QisRequestHandler.do_GET(handler)

    assert payloads == [(200, {"positions": [], "analyses": []})]


def test_deep_analysis_rank_route_returns_ranked_payload(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/deep-analysis/rank?days=80"
    payloads = []

    monkeypatch.setattr(
        QisRequestHandler,
        "_live_forecasts",
        lambda self: {"RANK-USDT": _rank_route_forecast()},
    )
    monkeypatch.setattr(
        QisRequestHandler,
        "_json",
        lambda self, payload, status=200: payloads.append((status, payload)),
    )

    QisRequestHandler.do_GET(handler)

    status, payload = payloads[0]
    assert status == 200
    assert payload["ok"] is True
    assert payload["ranking"]["ranked"][0]["inst_id"] == "RANK-USDT"
    assert payload["ranking"]["ranked"][0]["rank"] == 1


def test_deep_analysis_route_prefers_long_external_stock_history(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/deep-analysis?inst_id=TSLA-USDT-SWAP&days=180"
    payloads = []
    okx_mapping = _route_forecast(
        "TSLA-USDT-SWAP",
        symbol="TSLA",
        count=128,
        source="OKX ticker",
        market_type="股票映射行情",
    )
    yahoo_stock = _route_forecast(
        "TSLA-US",
        symbol="TSLA",
        count=220,
        source="Yahoo Finance 日线 · NMS",
        market_type="美股现货",
    )

    monkeypatch.setattr(
        QisRequestHandler,
        "_live_forecasts",
        lambda self, inst_ids=None: {
            "TSLA-USDT-SWAP": okx_mapping,
            "TSLA-US": yahoo_stock,
        },
    )
    monkeypatch.setattr("qis.web_server.fetch_deep_news", lambda symbol: [])
    monkeypatch.setattr(
        QisRequestHandler,
        "_json",
        lambda self, payload, status=200: payloads.append((status, payload)),
    )

    QisRequestHandler.do_GET(handler)

    status, payload = payloads[0]
    analysis = payload["analysis"]
    assert status == 200
    assert payload["ok"] is True
    assert analysis["inst_id"] == "TSLA-USDT-SWAP"
    assert analysis["market_type"] == "美股现货"
    assert "Yahoo Finance" in analysis["data_source"]
    assert analysis["range_days"] == 180


def test_spot_candles_route_prefers_long_external_stock_daily_history(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/spot/candles?inst_id=TSLA-USDT-SWAP&bar=1D"
    payloads = []
    okx_mapping = _route_forecast(
        "TSLA-USDT-SWAP",
        symbol="TSLA",
        count=128,
        source="OKX ticker",
        market_type="股票映射行情",
    )
    yahoo_stock = _route_forecast(
        "TSLA-US",
        symbol="TSLA",
        count=220,
        source="Yahoo Finance 日线 · NMS",
        market_type="美股现货",
    )

    class NoOkxClient:
        def __init__(self) -> None:
            raise AssertionError("OKX should not be called for external daily stock history")

    monkeypatch.setattr(
        QisRequestHandler,
        "_forecasts",
        lambda self: {
            "TSLA-USDT-SWAP": okx_mapping,
            "TSLA-US": yahoo_stock,
        },
    )
    monkeypatch.setattr("qis.web_server.OkxClient", NoOkxClient)
    monkeypatch.setattr(
        QisRequestHandler,
        "_json",
        lambda self, payload, status=200: payloads.append((status, payload)),
    )

    QisRequestHandler.do_GET(handler)

    status, payload = payloads[0]
    assert status == 200
    assert payload["ok"] is True
    assert payload["inst_id"] == "TSLA-USDT-SWAP"
    assert payload["source"] == "Yahoo Finance 日线 · NMS"
    assert payload["coverage"] == 220
    assert payload["analysis_source_inst_id"] == "TSLA-US"
    assert len(payload["candles"]) == 220


def test_spot_candles_route_merges_cached_and_okx_daily_history(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/spot/candles?inst_id=BTC-USDT&bar=1D"
    payloads = []
    cached = _route_forecast(
        "BTC-USDT",
        symbol="BTC",
        count=2,
        source="已收盘日K",
        market_type="现货",
    )
    okx_start = datetime(2025, 1, 2, tzinfo=timezone.utc)
    okx_candles = [
        Candle(okx_start, open=200, high=203, low=198, close=202, volume=2000),
        Candle(okx_start + timedelta(days=1), open=202, high=206, low=201, close=205, volume=2100),
    ]

    class StubOkxClient:
        @staticmethod
        def public_candles(inst_id: str, bar: str, limit: int) -> list[Candle]:
            assert inst_id == "BTC-USDT"
            assert bar == "1D"
            assert limit == 300
            return okx_candles

    monkeypatch.setattr(QisRequestHandler, "_forecasts", lambda self: {"BTC-USDT": cached})
    monkeypatch.setattr("qis.web_server.OkxClient", StubOkxClient)
    monkeypatch.setattr(
        QisRequestHandler,
        "_json",
        lambda self, payload, status=200: payloads.append((status, payload)),
    )

    QisRequestHandler.do_GET(handler)

    status, payload = payloads[0]
    assert status == 200
    assert payload["ok"] is True
    assert payload["coverage"] == 3
    assert payload["source"] == "OKX market candles"
    assert [item["date"][:10] for item in payload["candles"]] == [
        "2025-01-01",
        "2025-01-02",
        "2025-01-03",
    ]
    assert payload["candles"][1]["close"] == 202


def test_spot_candles_route_maps_one_day_range_to_five_minute_history(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/spot/candles?inst_id=BTC-USDT&range=1D"
    payloads = []
    forecast = _route_forecast(
        "BTC-USDT",
        symbol="BTC",
        count=220,
        source="OKX ticker",
        market_type="现货",
    )
    start = datetime(2026, 7, 19, tzinfo=timezone.utc)
    intraday = [
        Candle(start + timedelta(minutes=5 * index), 100, 102, 99, 101, 1000)
        for index in range(12)
    ]

    class StubOkxClient:
        @staticmethod
        def public_history_candles(inst_id: str, bar: str, limit: int) -> list[Candle]:
            assert inst_id == "BTC-USDT"
            assert bar == "5m"
            assert limit == 288
            return intraday

    monkeypatch.setattr(QisRequestHandler, "_forecasts", lambda self: {"BTC-USDT": forecast})
    monkeypatch.setattr("qis.web_server.OkxClient", StubOkxClient)
    monkeypatch.setattr(
        QisRequestHandler,
        "_json",
        lambda self, payload, status=200: payloads.append((status, payload)),
    )

    QisRequestHandler.do_GET(handler)

    status, payload = payloads[0]
    assert status == 200
    assert payload["range"] == "1D"
    assert payload["bar"] == "5m"
    assert payload["coverage"] == 12
    assert payload["from"] == intraday[0].ts.isoformat()
    assert payload["to"] == intraday[-1].ts.isoformat()


def test_candle_ranges_use_distinct_market_intervals() -> None:
    assert {
        key: CANDLE_RANGE_SPECS[key]["bar"]
        for key in ("1D", "1M", "3M", "6M")
    } == {"1D": "5m", "1M": "4H", "3M": "12H", "6M": "1D"}
    assert [CANDLE_RANGE_SPECS[key]["days"] for key in ("1D", "1M", "3M", "6M")] == [
        1,
        31,
        93,
        186,
    ]


def test_spot_candles_route_filters_external_equity_range_by_latest_candle(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/spot/candles?inst_id=TSLA-USDT-SWAP&range=1M"
    payloads = []
    mapped = _route_forecast(
        "TSLA-USDT-SWAP",
        symbol="TSLA",
        count=128,
        source="OKX ticker",
        market_type="股票映射行情",
    )
    yahoo = _route_forecast(
        "TSLA-US",
        symbol="TSLA",
        count=220,
        source="Yahoo Finance 日线 · NMS",
        market_type="美股现货",
    )

    class NoOkxClient:
        def __init__(self) -> None:
            raise AssertionError("OKX should not be called for external equity ranges")

    monkeypatch.setattr(
        QisRequestHandler,
        "_forecasts",
        lambda self: {"TSLA-USDT-SWAP": mapped, "TSLA-US": yahoo},
    )
    monkeypatch.setattr("qis.web_server.OkxClient", NoOkxClient)
    monkeypatch.setattr(
        QisRequestHandler,
        "_json",
        lambda self, payload, status=200: payloads.append((status, payload)),
    )

    QisRequestHandler.do_GET(handler)

    status, payload = payloads[0]
    assert status == 200
    assert payload["range"] == "1M"
    assert payload["bar"] == "1D"
    assert payload["coverage"] == 32
    assert payload["analysis_source_inst_id"] == "TSLA-US"


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
