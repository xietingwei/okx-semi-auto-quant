from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from qis.models import Side, Signal, TradePlan, utc_now
from qis.storage import Storage


def test_storage_counts_approved_trades_today(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    signal = Signal("BTC-USDT-SWAP", Side.BUY, 100.0, 95.0, 110.0, "test", 0.5, utc_now())
    storage.save_plan(TradePlan(signal, 1.0, 100.0, 5.0, 0.02, True, "approved"))
    storage.save_plan(TradePlan(signal, 0.0, 0.0, 0.0, 0.0, False, "rejected"))

    assert storage.approved_trades_today() == 1


def test_storage_records_manual_trade_stats(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    storage.record_manual_trade(
        inst_id="ETH-USDT-SWAP",
        side="buy",
        entry=100.0,
        exit_price=110.0,
        size=1.0,
        stop=95.0,
        take_profit=115.0,
        model="walkforward_calibrated_macro_intel_v4",
        estimated_probability=0.7,
        notes="test",
    )

    stats = storage.manual_trade_stats()

    assert stats["trades"] == 1
    assert stats["win_rate"] == 1.0
    assert stats["avg_r"] == 2.0


def test_spot_position_close_updates_reliability(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    position_id = storage.open_spot_position(
        inst_id="BTC-USDT",
        buy_price=100.0,
        quantity=2.0,
        horizon="1w",
        forecast_return=0.05,
        up_probability=0.65,
        confidence=0.7,
        target_price=105.0,
        notes="test",
    )

    assert storage.close_spot_position(position_id, 110.0)
    stats = storage.spot_reliability()

    assert stats["overall"]["trades"] == 1
    assert stats["overall"]["win_rate"] == 1.0
    assert stats["by_horizon"]["1w"]["direction_accuracy"] == 1.0


def test_spot_position_can_be_deleted(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    position_id = storage.open_spot_position(
        inst_id="ETH-USDT",
        buy_price=100.0,
        quantity=1.5,
        horizon="1m",
        forecast_return=0.08,
        up_probability=0.62,
        confidence=0.7,
        target_price=108.0,
        notes="delete me",
    )

    assert storage.delete_spot_position(position_id)
    assert not storage.delete_spot_position(position_id)
    assert [dict(row) for row in storage.spot_positions()] == []


def test_forecast_advice_uses_predictions_not_manual_trades(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    predicted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    forecast = {
        "inst_id": "BTC-USDT",
        "current_price": 100.0,
        "forecasts": [
            {
                "key": "1d",
                "days": 1,
                "target": 110.0,
                "low": 105.0,
                "high": 115.0,
                "expected_return": 0.10,
                "up_probability": 0.80,
                "confidence": 0.70,
            }
        ],
    }
    storage.record_forecast_snapshot(forecast, predicted_at)
    storage.evaluate_due_forecasts(
        {"BTC-USDT": 90.0},
        observed_at=predicted_at + timedelta(days=1),
    )

    evaluation = storage.forecast_evaluation()
    advice = storage.forecast_advice()

    assert evaluation["overall"]["samples"] == 1
    assert evaluation["by_horizon"]["1d"]["direction_accuracy"] == 0.0
    assert any("历史预测" in item["title"] for item in advice)


def test_hourly_forecast_snapshot_is_deduplicated(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    forecast = {
        "inst_id": "BTC-USDT",
        "current_price": 100.0,
        "forecasts": [{
            "key": "1d",
            "days": 1,
            "target": 105.0,
            "low": 95.0,
            "high": 110.0,
            "expected_return": 0.05,
            "up_probability": 0.65,
            "confidence": 0.70,
        }],
    }
    predicted_at = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)

    storage.record_forecast_snapshot(forecast, predicted_at)
    storage.record_forecast_snapshot(forecast, predicted_at)

    evaluation = storage.forecast_evaluation()
    assert evaluation["overall"]["pending"] == 1


def test_forecast_versions_can_share_same_hour_without_mixing_learning(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    predicted_at = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    base = {
        "inst_id": "BTC-USDT",
        "current_price": 100.0,
        "forecasts": [{
            "key": "1d",
            "days": 1,
            "target": 105.0,
            "low": 95.0,
            "high": 110.0,
            "expected_return": 0.05,
            "up_probability": 0.65,
            "confidence": 0.70,
        }],
    }

    storage.record_forecast_snapshot({**base, "model_version": "legacy_v1"}, predicted_at)
    storage.record_forecast_snapshot(base, predicted_at)

    evaluation = storage.forecast_evaluation()

    assert evaluation["overall"]["pending"] == 1


def test_forecast_learning_run_is_auditable(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    run_at = datetime(2026, 6, 18, 8, tzinfo=timezone.utc)
    evaluation = {
        "overall": {
            "samples": 120,
            "pending": 15,
            "direction_accuracy": 0.61,
        }
    }
    adjustments = {
        "1d": {"active": True, "samples": 50},
        "1w": {"active": False, "samples": 20},
    }
    advice = [{"level": "bias", "title": "修正偏差", "detail": "测试"}]

    storage.record_forecast_learning_run(
        run_at,
        7,
        evaluation,
        adjustments,
        advice,
    )

    latest = storage.latest_forecast_learning_run()
    assert latest is not None
    assert latest["evaluated_count"] == 7
    assert latest["total_samples"] == 120
    assert latest["active_horizons"] == 1
    assert latest["adjustments"]["1d"]["active"] is True


def test_forecast_calibration_isolated_by_instrument(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "qis.sqlite3")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    horizons = (("1d", 1), ("3d", 3), ("1w", 7), ("2w", 14))
    for index in range(32):
        direction = 1 if index % 2 == 0 else -1
        for inst_id, actual_direction in (
            ("BTC-USDT", direction),
            ("ETH-USDT", -direction),
        ):
            expected_return = direction * 0.05
            forecast = {
                "inst_id": inst_id,
                "current_price": 100.0,
                "forecasts": [
                    {
                        "key": key,
                        "days": days,
                        "target": 100.0 * (1 + expected_return),
                        "low": 90.0,
                        "high": 110.0,
                        "expected_return": expected_return,
                        "up_probability": 0.70 if direction > 0 else 0.30,
                        "confidence": 0.70,
                    }
                    for key, days in horizons
                ],
            }
            actual_prices = {
                key: 100.0 * (1 + actual_direction * 0.04)
                for key, _ in horizons
            }
            storage.record_historical_forecast_outcome(
                forecast,
                start + timedelta(days=index),
                actual_prices,
            )

    btc = storage.forecast_strategy_adjustments(inst_id="BTC-USDT")
    eth = storage.forecast_strategy_adjustments(inst_id="ETH-USDT")
    market = storage.forecast_strategy_adjustments()

    assert btc["3d"]["direction_accuracy"] > 0.99
    assert btc["3d"]["edge"] > 0.49
    assert eth["3d"]["direction_accuracy"] < 0.01
    assert market["3d"]["direction_accuracy"] == pytest.approx(0.5, abs=0.01)
    assert (
        btc["3d"]["calibration_method"]
        == "per_asset_direction_preserving_shrinkage_v4"
    )
