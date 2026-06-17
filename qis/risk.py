from __future__ import annotations

from dataclasses import dataclass

from qis.models import AccountState, Signal, TradePlan


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float
    daily_loss_limit: float
    max_drawdown: float
    max_leverage: float
    max_notional_pct: float
    max_trades_per_day: int


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def build_plan(self, signal: Signal, account: AccountState) -> TradePlan:
        if account.equity <= 0:
            return self._reject(signal, "equity is zero")
        if account.trades_today >= self.limits.max_trades_per_day:
            return self._reject(signal, "daily trade count limit reached")
        if account.daily_pnl <= -account.equity * self.limits.daily_loss_limit:
            return self._reject(signal, "daily loss limit reached")
        drawdown = 1 - account.equity / max(account.peak_equity, account.equity)
        if drawdown >= self.limits.max_drawdown:
            return self._reject(signal, "max drawdown limit reached")
        stop_distance = abs(signal.entry - signal.stop)
        if stop_distance <= 0:
            return self._reject(signal, "invalid stop distance")
        risk_amount = account.equity * self.limits.risk_per_trade
        raw_size = risk_amount / stop_distance
        max_notional = account.equity * self.limits.max_notional_pct
        max_levered_notional = account.equity * self.limits.max_leverage
        notional_cap = min(max_notional, max_levered_notional)
        size = min(raw_size, notional_cap / signal.entry)
        notional = size * signal.entry
        if size <= 0 or notional <= 5:
            return self._reject(signal, "position size is too small")
        leverage = notional / account.equity
        return TradePlan(
            signal=signal,
            size=size,
            notional=notional,
            risk_amount=min(risk_amount, size * stop_distance),
            leverage=leverage,
            approved=True,
            reason="approved",
        )

    @staticmethod
    def _reject(signal: Signal, reason: str) -> TradePlan:
        return TradePlan(
            signal=signal,
            size=0.0,
            notional=0.0,
            risk_amount=0.0,
            leverage=0.0,
            approved=False,
            reason=reason,
        )
