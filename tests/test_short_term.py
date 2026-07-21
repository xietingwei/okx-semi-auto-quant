from datetime import datetime, timedelta, timezone

from qis.models import Candle
from qis.forecast_learning import apply_strategy_adjustments
from qis.short_term import assess_short_term_data, short_term_context
from qis.spot_forecast import SpotForecastEngine
from qis.__main__ import _backfill_forecast_history


def _candles(count: int = 100) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            ts=start + timedelta(days=index),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.5 + index,
            volume=1000.0,
        )
        for index in range(count)
    ]


def test_short_term_quality_detects_duplicate_and_missing_bars() -> None:
    rows = _candles()
    rows.insert(20, rows[19])
    rows = rows[:40] + rows[42:]

    report = assess_short_term_data(rows, as_of=rows[-1].ts)

    assert report["duplicate_bars"] == 1
    assert report["gap_count"] == 1
    assert report["actionable"] is False
    assert "历史缺口" in "；".join(report["warnings"])


def test_short_term_quality_allows_normal_equity_weekend_gap() -> None:
    rows = _candles(150)
    rows = [item for item in rows if item.ts.weekday() < 5]

    report = assess_short_term_data(rows, as_of=rows[-1].ts, allow_weekends=True)

    assert report["gap_count"] == 0
    assert report["actionable"] is True


def test_short_term_context_exposes_validated_scope() -> None:
    quality = assess_short_term_data(_candles(), as_of=_candles()[-1].ts)

    context = short_term_context(quality)

    assert context["primary_horizon"] == "3d"
    assert context["confirmation_horizon"] == "1w"
    assert context["max_horizon"] == "2w"
    assert context["actionable"] is True


def test_forecast_canonicalises_duplicate_rows_and_exposes_quality() -> None:
    rows = _candles(100)
    rows = [rows[-1], *rows, rows[10]]

    forecast = SpotForecastEngine().analyze("BTC-USDT", rows)

    assert forecast is not None
    assert forecast.data_quality["duplicate_bars"] == 1
    assert forecast.short_term_context["max_horizon"] == "2w"
    assert forecast.short_term_context["actionable"] is True


def test_bad_history_cannot_issue_rebound_decision() -> None:
    rows = _candles(110)
    rows = rows[:55] + rows[57:]

    forecast = SpotForecastEngine().analyze("BTC-USDT", rows)

    assert forecast is not None
    assert forecast.data_quality["actionable"] is False
    assert forecast.decision == "历史数据质量不足，观望"


def test_backfill_uses_canonical_history_and_complete_short_horizons() -> None:
    rows = _candles(125)
    rows = [rows[-1], *rows, rows[14]]

    class CaptureStorage:
        def __init__(self) -> None:
            self.calls = []

        def record_historical_forecast_outcome(self, forecast, predicted_at, actual_prices):
            self.calls.append((forecast, predicted_at, actual_prices))

    storage = CaptureStorage()
    _backfill_forecast_history(storage, SpotForecastEngine(), "BTC-USDT", rows)

    assert storage.calls
    assert all(
        set(actual_prices) == {"1d", "3d", "1w", "2w"}
        for _, _, actual_prices in storage.calls
    )
    assert [call[1] for call in storage.calls] == sorted(call[1] for call in storage.calls)


def test_calibration_cannot_restore_signal_from_bad_history() -> None:
    forecast = {
        "current_price": 100.0,
        "volatility": 0.02,
        "market_context": {},
        "data_quality": {"actionable": False, "warnings": ["检测到历史缺口"]},
        "forecasts": [
            {
                "key": key,
                "days": days,
                "target": 110.0,
                "low": 90.0,
                "high": 120.0,
                "expected_return": 0.08,
                "up_probability": 0.70,
                "confidence": 0.65,
                "signal": "偏多",
            }
            for key, days in (("1d", 1), ("3d", 3), ("1w", 7), ("2w", 14))
        ],
    }

    result = apply_strategy_adjustments(forecast, {})

    assert result["opportunity_score"] <= 39
    assert result["decision"] == "历史数据质量不足，观望"
