import pytest
from datetime import datetime, timedelta, timezone

from qis.web_server import QisRequestHandler, _rebase_forecast, _rebase_forecast_prices


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


def test_shadow_brain_rank_route_returns_cached_ranking(monkeypatch) -> None:
    handler = QisRequestHandler.__new__(QisRequestHandler)
    handler.path = "/api/shadow-brain/rank"
    payloads = []
    forecast = _rank_route_forecast()
    forecast["shadow_brain"] = {
        "status": "shadow_running",
        "direction": "up",
        "projection_gate": "watch",
        "up_probability": 0.58,
        "confidence": 0.42,
        "expected_return_5d": 0.015,
        "validation_accuracy": 0.56,
        "edge": 0.04,
        "samples": 90,
        "reason": "影子运行",
    }

    monkeypatch.setattr(
        QisRequestHandler,
        "_live_forecasts",
        lambda self: {"RANK-USDT": forecast},
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
    assert payload["ranking"]["model_version"] == "shadow_mlp_v1"
    assert payload["ranking"]["ranked"][0]["inst_id"] == "RANK-USDT"


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
