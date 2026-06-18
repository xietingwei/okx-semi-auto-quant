from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Mapping, Any


HORIZON_DAYS = {"1d": 1, "1w": 7, "1m": 30, "3m": 90, "6m": 180}


def analyze_position(position: Mapping[str, Any], forecast: Mapping[str, Any], now: datetime | None = None) -> dict:
    """Create an explainable, volatility-aware exit recommendation for an open position."""
    now = now or datetime.now(timezone.utc)
    buy_time = datetime.fromisoformat(str(position["buy_time"]).replace("Z", "+00:00"))
    if buy_time.tzinfo is None:
        buy_time = buy_time.replace(tzinfo=timezone.utc)

    entry = float(position["buy_price"])
    quantity = float(position["quantity"])
    current = float(forecast["current_price"])
    volatility = max(float(forecast.get("volatility", 0.0)), 0.002)
    invalidation = float(forecast.get("invalidation", entry * 0.94))
    horizon = str(position["horizon"])
    horizon_days = HORIZON_DAYS.get(horizon, 30)
    held_days = max(0.0, (now - buy_time).total_seconds() / 86400)
    selected = next(
        (item for item in forecast.get("forecasts", []) if item.get("key") == horizon),
        forecast.get("forecasts", [{}])[0],
    )
    up_probability = float(selected.get("up_probability", position["up_probability"]))
    target = float(selected.get("target", position["target_price"]))
    expected_return = float(selected.get("expected_return", position["forecast_return"]))
    history = forecast.get("history", [])
    entry_date = buy_time.date().isoformat()
    closes_since_entry = [
        float(item["close"]) for item in history if str(item.get("date", "")) >= entry_date
    ]
    high_since_entry = max([entry, current, *closes_since_entry])

    current_return = current / entry - 1
    peak_return = high_since_entry / entry - 1
    drawdown_from_peak = current / high_since_entry - 1

    # Volatility-scaled loss budget: tighter for short horizons, never wider than 12%.
    horizon_scale = min(2.2, math.sqrt(max(horizon_days, 1)))
    loss_budget = min(0.12, max(0.025, volatility * horizon_scale * 1.65))
    hard_stop = entry * (1 - loss_budget)
    trail_distance = min(0.16, max(0.025, volatility * horizon_scale * 1.45))
    trailing_stop = high_since_entry * (1 - trail_distance)
    breakeven_stop = entry * 1.002 if peak_return >= loss_budget * 1.2 else 0.0
    suggested_stop = max(invalidation, hard_stop, trailing_stop, breakeven_stop)

    stop_breached = current <= suggested_stop
    target_reached = current >= target or current_return >= max(expected_return * 0.9, loss_budget * 1.5)
    probability_weak = up_probability < 0.48
    trend_weak = "下降" in str(forecast.get("regime", "")) or str(selected.get("signal")) == "偏空"
    horizon_expired = held_days >= horizon_days * 1.15
    deep_drawdown = drawdown_from_peak <= -max(loss_budget * 0.8, 0.03)

    risk_score = 0
    risk_score += 45 if stop_breached else min(25, max(0, -current_return / loss_budget * 25))
    risk_score += 18 if deep_drawdown else min(12, max(0, -drawdown_from_peak / trail_distance * 12))
    risk_score += 14 if probability_weak else max(0, (0.58 - up_probability) * 70)
    risk_score += 13 if trend_weak else 0
    risk_score += 10 if horizon_expired else max(0, (held_days / max(horizon_days, 1) - 0.8) * 20)
    risk_score = round(min(100, risk_score))

    reasons: list[str] = []
    if stop_breached:
        reasons.append("价格已触及波动率动态保护位")
    if deep_drawdown:
        reasons.append("从持仓高点回撤扩大")
    if probability_weak:
        reasons.append("原周期上涨概率已降至中性以下")
    if trend_weak:
        reasons.append("趋势状态转弱")
    if horizon_expired:
        reasons.append("已超过原预测持有周期")
    if target_reached:
        reasons.append("原预测目标已基本实现")

    if stop_breached:
        action, label = "exit", "止损退出"
    elif risk_score >= 72:
        action, label = "exit", "建议卖出"
    elif target_reached or risk_score >= 52:
        action, label = "reduce", "止盈减仓" if target_reached else "分批减仓"
    elif risk_score >= 30 or peak_return >= loss_budget:
        action, label = "protect", "收紧保护"
    else:
        action, label = "hold", "继续持有"
    if not reasons:
        reasons.append("趋势、概率与波动风险仍在可接受范围")

    return {
        "position_id": int(position["id"]),
        "action": action,
        "action_label": label,
        "risk_score": risk_score,
        "reason": "；".join(reasons[:3]),
        "current_price": current,
        "current_return": current_return,
        "unrealized_pnl": (current - entry) * quantity,
        "suggested_stop": suggested_stop,
        "target_price": target,
        "up_probability": up_probability,
        "held_days": held_days,
        "horizon_days": horizon_days,
        "drawdown_from_peak": drawdown_from_peak,
        "model": "volatility_trailing_regime_exit_v1",
    }
