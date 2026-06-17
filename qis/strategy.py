from __future__ import annotations

from statistics import mean

from qis.models import Candle, Side, Signal, utc_now


class DonchianBreakoutStrategy:
    def __init__(
        self,
        lookback: int = 20,
        atr_period: int = 14,
        atr_multiplier: float = 1.8,
        ema_fast: int = 0,
        ema_slow: int = 0,
    ) -> None:
        self.lookback = lookback
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def generate(self, inst_id: str, candles: list[Candle]) -> Signal | None:
        required = max(self.lookback + 2, self.atr_period + 2, self.ema_slow + 2)
        if len(candles) < required:
            return None
        closed = candles[:-1]
        latest = closed[-1]
        window = closed[-self.lookback - 1 : -1]
        upper = max(item.high for item in window)
        lower = min(item.low for item in window)
        atr = self._atr(closed[-self.atr_period - 1 :])
        if atr <= 0:
            return None
        use_ema_filter = self.ema_fast > 0 and self.ema_slow > 0
        closes = [item.close for item in closed]
        ema_fast = self._ema(closes, self.ema_fast) if use_ema_filter else 0.0
        ema_slow = self._ema(closes, self.ema_slow) if use_ema_filter else 0.0
        if latest.close > upper:
            if use_ema_filter and ema_fast <= ema_slow:
                return None
            stop = latest.close - atr * self.atr_multiplier
            return Signal(
                inst_id=inst_id,
                side=Side.BUY,
                entry=latest.close,
                stop=stop,
                take_profit=latest.close + (latest.close - stop) * 2,
                reason=self._reason(latest.close, "high", upper, use_ema_filter, ">"),
                confidence=0.58,
                created_at=utc_now(),
            )
        if latest.close < lower:
            if use_ema_filter and ema_fast >= ema_slow:
                return None
            stop = latest.close + atr * self.atr_multiplier
            return Signal(
                inst_id=inst_id,
                side=Side.SELL,
                entry=latest.close,
                stop=stop,
                take_profit=latest.close - (stop - latest.close) * 2,
                reason=self._reason(latest.close, "low", lower, use_ema_filter, "<"),
                confidence=0.58,
                created_at=utc_now(),
            )
        return None

    @staticmethod
    def _atr(candles: list[Candle]) -> float:
        ranges = []
        for prev, curr in zip(candles, candles[1:]):
            true_range = max(
                curr.high - curr.low,
                abs(curr.high - prev.close),
                abs(curr.low - prev.close),
            )
            ranges.append(true_range)
        return mean(ranges) if ranges else 0.0

    @staticmethod
    def _ema(values: list[float], period: int) -> float:
        if not values:
            return 0.0
        if period <= 0:
            return values[-1]
        alpha = 2 / (period + 1)
        ema = values[0]
        for value in values[1:]:
            ema = value * alpha + ema * (1 - alpha)
        return ema

    def _reason(self, close: float, boundary_name: str, boundary: float, use_ema_filter: bool, op: str) -> str:
        reason = f"close {close:.2f} broke {self.lookback}-bar {boundary_name} {boundary:.2f}"
        if use_ema_filter:
            reason += f"; EMA{self.ema_fast}{op}{self.ema_slow}"
        return reason
