from datetime import datetime, timedelta, timezone

import pytest

from qis.deep_analysis import DeepAnalysisEngine, NewsItem, rank_deep_analyses


def _forecast(count: int = 150) -> dict:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    history = []
    for index in range(count):
        drift = 0.0018 if index % 28 < 17 else -0.0009
        if index in {44, 87, 121}:
            drift = 0.045
        if index in {65, 112}:
            drift = -0.035
        open_price = price
        price *= 1 + drift
        high = max(open_price, price) * (1.011 if drift >= 0 else 1.004)
        low = min(open_price, price) * (0.989 if drift < 0 else 0.996)
        history.append(
            {
                "date": (start + timedelta(days=index)).date().isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": price,
                "volume": 1000 + index * 4 + (900 if abs(drift) > 0.03 else 0),
            }
        )

    return {
        "inst_id": "NVDA-USDT",
        "symbol": "NVDA",
        "market_type": "美股现货",
        "data_source": "Yahoo Finance daily",
        "current_price": price,
        "history": history,
        "forecasts": [
            {
                "key": "1w",
                "expected_return": 0.04,
                "up_probability": 0.62,
            }
        ],
    }


def _low_confidence_forecast(count: int = 150) -> dict:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    history = []
    for index in range(count):
        drift = 0.04 if index % 10 == 0 else -0.03 if index % 10 == 5 else 0.0
        open_price = price
        price *= 1 + drift
        history.append(
            {
                "date": (start + timedelta(days=index)).date().isoformat(),
                "open": open_price,
                "high": max(open_price, price) * 1.002,
                "low": min(open_price, price) * 0.998,
                "close": price,
                "volume": 1000 + (500 if abs(drift) > 0.02 else 0),
            }
        )

    return {
        "inst_id": "NOISE-USDT",
        "symbol": "NOISE",
        "market_type": "现货",
        "data_source": "Synthetic daily",
        "current_price": price,
        "history": history,
        "forecasts": [{"key": "1w", "expected_return": 0.02}],
    }


def test_deep_analysis_builds_daily_reviews_and_super_brain() -> None:
    forecast = _forecast()
    news_day = datetime.fromisoformat(forecast["history"][-20]["date"]).replace(
        tzinfo=timezone.utc
    )
    news = [
        NewsItem(
            published_at=news_day,
            title="NVDA shares surge after record data center growth",
            source="Yahoo Finance",
            url="https://finance.yahoo.com/example",
        )
    ]

    result = DeepAnalysisEngine().analyze(forecast, news=news, max_days=80)

    assert result["inst_id"] == "NVDA-USDT"
    assert result["range_days"] == 80
    assert result["daily"][0]["date"] == forecast["history"][-1]["date"]
    assert result["quality_gate"]["tested_hypotheses"] > 0
    assert result["quality_gate"]["external_news_items"] == 1
    assert result["super_brain"]
    assert result["scenarios"][0]["probability"] > 0
    assert any(day["events"] for day in result["daily"])
    assert all(day["hypotheses"][0]["validation"]["status"] for day in result["daily"])


def test_deep_analysis_accepts_six_month_daily_window() -> None:
    forecast = _forecast(count=220)

    result = DeepAnalysisEngine().analyze(forecast, max_days=180)

    assert result["range_days"] == 180
    assert result["quality_gate"]["daily_coverage"] == 180


def test_deep_analysis_includes_shadow_brain_payload() -> None:
    forecast = _forecast()
    forecast["shadow_brain"] = {
        "status": "shadow_running",
        "projection_gate": "watch",
        "confidence": 0.42,
    }

    result = DeepAnalysisEngine().analyze(forecast, max_days=80)

    assert result["shadow_brain"]["status"] == "shadow_running"
    assert result["shadow_brain"]["projection_gate"] == "watch"


def test_deep_analysis_rejects_short_history() -> None:
    forecast = _forecast(count=20)

    with pytest.raises(ValueError, match="35"):
        DeepAnalysisEngine().analyze(forecast)


def test_super_brain_marks_low_accuracy_patterns_unusable_for_projection() -> None:
    daily = []
    for index in range(10):
        daily.append(
            {
                "date": f"2026-01-{index + 1:02d}",
                "pattern": {
                    "id": "transition",
                    "name": "过渡震荡",
                    "direction": "neutral",
                    "evidence": ["量价结构未形成明确优势"],
                    "invalidation": "等待突破或跌破后重新判断",
                },
                "hypotheses": [
                    {
                        "validation": {
                            "status": "confirmed" if index < 4 else "rejected",
                            "return_5d": 0.01,
                            "max_drawdown_5d": -0.03,
                        }
                    }
                ],
            }
        )

    row = DeepAnalysisEngine()._super_brain(daily)[0]

    assert row["quality_tier"] == "rejected"
    assert row["usable_for_projection"] is False
    assert row["verdict"] == "暂不进入核心大脑"


def test_low_quality_current_pattern_downgrades_future_scenarios() -> None:
    latest = {
        "pattern": {
            "id": "transition",
            "name": "过渡震荡",
            "invalidation": "等待突破或跌破后重新判断",
        }
    }
    patterns = [
        {
            "pattern_id": "transition",
            "name": "过渡震荡",
            "success_rate": 0.35,
            "avg_5d_return": 0.01,
            "samples": 48,
            "usable_for_projection": False,
            "verdict": "暂不进入核心大脑",
        }
    ]
    forecast = {"forecasts": [{"key": "1w", "expected_return": 0.03}]}

    scenarios = DeepAnalysisEngine()._scenarios(latest, patterns, forecast)

    assert scenarios[0]["direction"] == "低可信观望"
    assert scenarios[0]["probability"] > scenarios[1]["probability"]
    assert "未通过核心门槛" in scenarios[0]["reason"]


def test_rank_deep_analyses_prioritizes_projection_ready_symbols() -> None:
    ranking = rank_deep_analyses(
        [_low_confidence_forecast(), _forecast()],
        max_days=80,
    )

    rows = ranking["ranked"]

    assert [row["rank"] for row in rows] == [1, 2]
    assert rows[0]["inst_id"] == "NVDA-USDT"
    assert rows[0]["projection_ready"] is True
    assert rows[0]["core_validation_rate"] >= 0.6
    assert rows[0]["rank_score"] > rows[1]["rank_score"]
    assert rows[1]["inst_id"] == "NOISE-USDT"
    assert rows[1]["status"] == "低可信观察"
    assert rows[1]["core_patterns"] == 0
