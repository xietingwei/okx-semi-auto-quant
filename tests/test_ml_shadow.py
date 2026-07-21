from __future__ import annotations

from datetime import datetime, timedelta, timezone

from qis.ml_shadow import attach_shadow_brain, rank_shadow_brains


def _forecast(inst_id: str, count: int, drift: float, *, shock_every: int = 0) -> dict:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    history = []
    for index in range(count):
        daily_drift = drift
        if shock_every and index % shock_every == 0:
            daily_drift *= -3
        open_price = price
        price *= 1 + daily_drift
        high = max(open_price, price) * 1.006
        low = min(open_price, price) * 0.994
        history.append(
            {
                "date": (start + timedelta(days=index)).date().isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": price,
                "volume": 1000 + index * 5 + (500 if daily_drift < 0 else 0),
            }
        )
    return {
        "inst_id": inst_id,
        "symbol": inst_id.split("-")[0],
        "market_type": "现货",
        "current_price": price,
        "history": history,
        "forecasts": [{"key": "1w", "expected_return": drift * 5}],
    }


def test_shadow_brain_attaches_prediction_for_sufficient_history() -> None:
    forecasts = [_forecast("TREND-USDT", 180, 0.002)]

    result = attach_shadow_brain(forecasts)

    assert result[0]["inst_id"] == "TREND-USDT"
    brain = result[0]["shadow_brain"]
    assert brain["model_version"] == "shadow_mlp_v2_temporal_split"
    assert brain["status"] == "shadow_running"
    assert brain["samples"] >= 60
    assert brain["projection_gate"] in {"allowed", "watch", "blocked"}
    assert brain["direction"] in {"up", "down", "neutral"}
    assert 0.0 <= brain["up_probability"] <= 1.0
    assert 0.0 <= brain["confidence"] <= 1.0


def test_shadow_brain_blocks_short_history() -> None:
    result = attach_shadow_brain([_forecast("SHORT-USDT", 45, 0.002)])

    brain = result[0]["shadow_brain"]
    assert brain["status"] == "insufficient_data"
    assert brain["projection_gate"] == "blocked"
    assert "90" in brain["reason"]


def test_shadow_brain_ranking_prioritizes_validated_edge() -> None:
    rows = attach_shadow_brain(
        [
            _forecast("NOISE-USDT", 180, 0.001, shock_every=4),
            _forecast("TREND-USDT", 180, 0.002),
        ]
    )

    ranking = rank_shadow_brains(rows)

    assert ranking["model_version"] == "shadow_mlp_v2_temporal_split"
    assert ranking["total"] == 2
    assert ranking["ranked"][0]["rank"] == 1
    assert ranking["ranked"][0]["shadow_score"] >= ranking["ranked"][1]["shadow_score"]
