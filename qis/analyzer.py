from __future__ import annotations

from dataclasses import dataclass
import math
from qis.external_intel import ExternalIntel
from qis.macro import MacroRegime
from qis.models import Candle, Side
from qis.strategy import DonchianBreakoutStrategy


@dataclass(frozen=True)
class Opportunity:
    inst_id: str
    side: Side
    status: str
    close: float
    entry_low: float
    entry_high: float
    stop: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    success_probability: float
    sample_size: int
    expected_r: float
    score: float
    regime: str
    macro_label: str
    macro_score: float
    intel_label: str
    intel_score: float
    model: str
    feature_quality: float
    distance_to_entry_pct: float
    reason: str


class MarketAnalyzer:
    def __init__(
        self,
        lookback: int = 20,
        atr_period: int = 14,
        atr_multiplier: float = 1.8,
        max_hold_bars: int = 48,
        macro: MacroRegime | None = None,
        intel: ExternalIntel | None = None,
    ) -> None:
        self.lookback = lookback
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_hold_bars = max_hold_bars
        self.macro = macro or MacroRegime("neutral", 0.0, {}, "macro not loaded")
        self.intel = intel or ExternalIntel("mixed", 0.0, [], "external intel not loaded", "")

    def analyze(self, inst_id: str, candles: list[Candle]) -> list[Opportunity]:
        required = max(self.lookback + self.atr_period + 4, 80)
        if len(candles) < required:
            return []
        closed = candles[:-1]
        latest = closed[-1]
        atr = DonchianBreakoutStrategy._atr(closed[-self.atr_period - 1 :])
        if atr <= 0:
            return []
        upper = max(item.high for item in closed[-self.lookback - 1 : -1])
        lower = min(item.low for item in closed[-self.lookback - 1 : -1])
        regime = self._regime(closed)
        long = self._build(inst_id, Side.BUY, latest.close, upper, atr, closed, regime)
        short = self._build(inst_id, Side.SELL, latest.close, lower, atr, closed, regime)
        return sorted([long, short], key=lambda item: item.score, reverse=True)

    def _build(
        self,
        inst_id: str,
        side: Side,
        close: float,
        boundary: float,
        atr: float,
        candles: list[Candle],
        regime: str,
    ) -> Opportunity:
        active = close >= boundary if side is Side.BUY else close <= boundary
        status = "active" if active else "watch"
        band = max(atr * 0.12, boundary * 0.0015)
        if side is Side.BUY:
            entry_low = boundary - band
            entry_high = boundary + band
            stop = boundary - atr * self.atr_multiplier
            risk = max(boundary - stop, atr)
            take_profit_1 = boundary + risk * 1.5
            take_profit_2 = boundary + risk * 2.2
            distance = max(0.0, (boundary - close) / close)
        else:
            entry_low = boundary - band
            entry_high = boundary + band
            stop = boundary + atr * self.atr_multiplier
            risk = max(stop - boundary, atr)
            take_profit_1 = boundary - risk * 1.5
            take_profit_2 = boundary - risk * 2.2
            distance = max(0.0, (close - boundary) / close)
        probability, sample, feature_quality = self._similarity_probability(candles, side, boundary, atr)
        macro_delta = self._macro_delta(side)
        intel_delta = self._intel_delta(side)
        probability = self._clip(probability + macro_delta + intel_delta, 0.05, 0.85)
        risk_reward = 2.2
        expected_r = probability * risk_reward - (1 - probability)
        reliability = min(1.0, sample / 28) * (0.55 + feature_quality * 0.45)
        proximity = max(0.0, 1 - distance / 0.015)
        regime_bonus = self._regime_bonus(side, regime)
        macro_bonus = self._macro_bonus(side)
        intel_bonus = self._intel_bonus(side)
        score = (
            expected_r * 45
            + probability * 35
            + proximity * 15
            + regime_bonus * 5
            + macro_bonus * 8
            + intel_bonus * 7
        ) * reliability
        model = "similarity_bayes_macro_intel_v3"
        reason = (
            f"{status} {side.value} breakout near {boundary:.4f}; "
            f"ATR={atr:.4f}; regime={regime}; macro={self.macro.label}({self.macro.risk_score:.2f}); "
            f"intel={self.intel.label}({self.intel.score:.2f}); sample={sample}; quality={feature_quality:.2f}"
        )
        return Opportunity(
            inst_id=inst_id,
            side=side,
            status=status,
            close=close,
            entry_low=entry_low,
            entry_high=entry_high,
            stop=stop,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            risk_reward=risk_reward,
            success_probability=probability,
            sample_size=sample,
            expected_r=expected_r,
            score=score,
            regime=regime,
            macro_label=self.macro.label,
            macro_score=self.macro.risk_score,
            intel_label=self.intel.label,
            intel_score=self.intel.score,
            model=model,
            feature_quality=feature_quality,
            distance_to_entry_pct=distance,
            reason=reason,
        )

    def _similarity_probability(self, candles: list[Candle], side: Side, boundary: float, atr: float) -> tuple[float, int, float]:
        current_features = self._features(candles, side, boundary, atr)
        weighted_wins = 0.0
        total_weight = 0.0
        squared_weight = 0.0
        trials = 0
        end = len(candles) - self.max_hold_bars - 1
        for i in range(max(self.lookback + self.atr_period + 2, 40), end):
            history = candles[: i + 1]
            latest = history[-1]
            window = history[-self.lookback - 1 : -1]
            upper = max(item.high for item in window)
            lower = min(item.low for item in window)
            atr = DonchianBreakoutStrategy._atr(history[-self.atr_period - 1 :])
            if atr <= 0:
                continue
            boundary_i = upper if side is Side.BUY else lower
            if not self._near_setup(side, latest.close, boundary_i, atr):
                continue
            entry = boundary_i
            risk = atr * self.atr_multiplier
            stop = entry - risk if side is Side.BUY else entry + risk
            target = entry + risk * 2.2 if side is Side.BUY else entry - risk * 2.2
            outcome = self._outcome(side, stop, target, candles[i + 1 : i + 1 + self.max_hold_bars])
            if outcome is None:
                continue
            sample_features = self._features(history, side, boundary_i, atr)
            distance = self._feature_distance(current_features, sample_features)
            weight = math.exp(-distance * 0.85)
            trials += 1
            total_weight += weight
            squared_weight += weight * weight
            weighted_wins += weight if outcome else 0.0
        if trials == 0 or total_weight <= 0:
            return 0.5, 0, 0.0
        effective_n = (total_weight * total_weight / squared_weight) if squared_weight else 0.0
        prior_mean = 0.48
        prior_strength = 8.0
        probability = (weighted_wins + prior_mean * prior_strength) / (total_weight + prior_strength)
        quality = min(1.0, effective_n / 18)
        return probability, trials, quality

    def _features(self, candles: list[Candle], side: Side, boundary: float, atr: float) -> tuple[float, ...]:
        latest = candles[-1]
        closes = [item.close for item in candles]
        signed = 1 if side is Side.BUY else -1
        breakout_z = signed * (latest.close - boundary) / atr
        ema_fast = DonchianBreakoutStrategy._ema(closes[-80:], 21)
        ema_slow = DonchianBreakoutStrategy._ema(closes[-120:], 55)
        trend_z = signed * (ema_fast - ema_slow) / atr
        lookback = min(6, len(closes) - 1)
        momentum_z = signed * (latest.close - closes[-1 - lookback]) / atr if lookback > 0 else 0.0
        atr_pct = atr / latest.close if latest.close else 0.0
        rsi = self._rsi(closes[-20:])
        rsi_z = signed * ((rsi - 50) / 25)
        avg_volume = sum(item.volume for item in candles[-30:]) / min(30, len(candles))
        volume_z = latest.volume / avg_volume - 1 if avg_volume else 0.0
        return (
            self._clip(breakout_z, -3, 3),
            self._clip(trend_z, -5, 5),
            self._clip(momentum_z, -5, 5),
            self._clip(atr_pct * 100, 0, 8),
            self._clip(rsi_z, -2, 2),
            self._clip(volume_z, -2, 4),
        )

    @staticmethod
    def _feature_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        weights = (1.4, 1.2, 1.0, 0.7, 0.9, 0.5)
        return math.sqrt(sum(weight * (a - b) ** 2 for weight, a, b in zip(weights, left, right)))

    @staticmethod
    def _near_setup(side: Side, close: float, boundary: float, atr: float) -> bool:
        if side is Side.BUY:
            return close >= boundary - atr * 1.1
        return close <= boundary + atr * 1.1

    @staticmethod
    def _rsi(values: list[float]) -> float:
        if len(values) < 3:
            return 50.0
        gains = []
        losses = []
        for prev, curr in zip(values, values[1:]):
            change = curr - prev
            gains.append(max(change, 0.0))
            losses.append(abs(min(change, 0.0)))
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _outcome(side: Side, stop: float, target: float, future: list[Candle]) -> bool | None:
        for candle in future:
            if side is Side.BUY:
                if candle.low <= stop:
                    return False
                if candle.high >= target:
                    return True
            else:
                if candle.high >= stop:
                    return False
                if candle.low <= target:
                    return True
        return None

    @staticmethod
    def _regime(candles: list[Candle]) -> str:
        closes = [item.close for item in candles]
        ema_fast = DonchianBreakoutStrategy._ema(closes[-80:], 21)
        ema_slow = DonchianBreakoutStrategy._ema(closes[-120:], 55)
        atr = DonchianBreakoutStrategy._atr(candles[-15:])
        spread = abs(ema_fast - ema_slow) / closes[-1]
        atr_pct = atr / closes[-1] if closes[-1] else 0.0
        if spread < atr_pct * 0.35:
            return "range"
        if ema_fast > ema_slow:
            return "uptrend"
        return "downtrend"

    @staticmethod
    def _regime_bonus(side: Side, regime: str) -> float:
        if side is Side.BUY and regime == "uptrend":
            return 1.0
        if side is Side.SELL and regime == "downtrend":
            return 1.0
        if regime == "range":
            return 0.35
        return -0.25

    def _macro_delta(self, side: Side) -> float:
        directional = self.macro.risk_score if side is Side.BUY else -self.macro.risk_score
        return max(-0.08, min(0.08, directional * 0.10))

    def _macro_bonus(self, side: Side) -> float:
        directional = self.macro.risk_score if side is Side.BUY else -self.macro.risk_score
        return max(-1.0, min(1.0, directional))

    def _intel_delta(self, side: Side) -> float:
        directional = self.intel.score if side is Side.BUY else -self.intel.score
        return max(-0.06, min(0.06, directional * 0.08))

    def _intel_bonus(self, side: Side) -> float:
        directional = self.intel.score if side is Side.BUY else -self.intel.score
        return max(-1.0, min(1.0, directional))


def summarize_opportunities(opportunities: list[Opportunity], limit: int = 10) -> str:
    if not opportunities:
        return "No opportunities found."
    header = (
        "rank inst side status entry_zone stop tp1 tp2 success sample quality expR score regime macro intel model"
    )
    lines = [header]
    for idx, item in enumerate(sorted(opportunities, key=lambda row: row.score, reverse=True)[:limit], start=1):
        lines.append(
            f"{idx} {item.inst_id} {item.side.value} {item.status} "
            f"{item.entry_low:.4f}-{item.entry_high:.4f} {item.stop:.4f} "
            f"{item.take_profit_1:.4f} {item.take_profit_2:.4f} "
            f"{item.success_probability * 100:.1f}% {item.sample_size} "
            f"{item.feature_quality:.2f} {item.expected_r:.2f} {item.score:.1f} "
            f"{item.regime} {item.macro_label}:{item.macro_score:.2f} "
            f"{item.intel_label}:{item.intel_score:.2f} {item.model}"
        )
    return "\n".join(lines)
