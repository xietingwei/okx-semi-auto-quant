from datetime import datetime, timedelta, timezone

import pytest

from qis.position_risk import analyze_position


def _position(**changes):
    row = {
        "id": 1,
        "inst_id": "BTC-USDT",
        "buy_time": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "buy_price": 100.0,
        "quantity": 2.0,
        "horizon": "1w",
        "forecast_return": 0.08,
        "up_probability": 0.65,
        "target_price": 108.0,
    }
    row.update(changes)
    return row


def _forecast(price=104.0, probability=0.66, regime="上升趋势"):
    return {
        "current_price": price,
        "volatility": 0.015,
        "invalidation": 96.0,
        "regime": regime,
        "history": [{"date": "2026-01-01", "close": 100.0}],
        "forecasts": [{
            "key": "1w",
            "target": 108.0,
            "expected_return": 0.08,
            "up_probability": probability,
            "signal": "偏多" if probability >= 0.5 else "偏空",
        }],
    }


def test_position_risk_holds_healthy_position() -> None:
    result = analyze_position(_position(), _forecast())

    assert result["action"] in {"hold", "protect"}
    assert result["current_return"] == pytest.approx(0.04)
    assert result["suggested_stop"] > 96.0
    assert "防守线" in result["sell_advice"] or "有效跌破" in result["sell_advice"]
    assert result["stop_distance"] < 0


def test_position_risk_exits_when_dynamic_stop_breaks() -> None:
    result = analyze_position(_position(), _forecast(price=93.0, probability=0.35, regime="下降趋势"))

    assert result["action"] == "exit"
    assert result["action_label"] == "止损退出"
    assert result["risk_score"] >= 72
    assert result["timing_label"] == "立即执行"
    assert "不要把止损位" in result["sell_advice"]
