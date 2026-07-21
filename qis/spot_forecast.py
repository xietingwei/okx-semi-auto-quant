from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from statistics import mean, pstdev

from qis.models import Candle
from qis.short_term import assess_short_term_data, canonicalize_candles, short_term_context


FORECAST_MODEL_VERSION = "short_horizon_evidence_gate_v8"
FORECAST_HISTORY_LIMIT = 200
FORECAST_HORIZONS = (
    ("1d", "1天", 1),
    ("3d", "3天", 3),
    ("1w", "7天", 7),
    ("2w", "14天", 14),
)
FORECAST_LABELS = {key: label for key, label, _ in FORECAST_HORIZONS}

STRATEGY_CATALOG = (
    {
        "id": "adaptive",
        "name": "综合自适应",
        "focus": "1–14天",
        "direction": "以3天和7天为主，兼顾盘口、量能与大环境",
        "best_for": "短线方向尚未完全统一、需要综合判断的行情",
        "risk": "极端事件下各类因子可能同时失效",
    },
    {
        "id": "trend",
        "name": "趋势跟随",
        "focus": "3–14天",
        "direction": "沿7/14/30日局部趋势跟随",
        "best_for": "短线单边趋势和回撤后续涨",
        "risk": "震荡市容易反复止损",
    },
    {
        "id": "breakout",
        "name": "突破确认",
        "focus": "1–7天",
        "direction": "跟随放量突破与盘口确认",
        "best_for": "动量启动、量价和持仓同步扩张",
        "risk": "假突破和流动性骤降",
    },
    {
        "id": "mean_reversion",
        "name": "均值回归",
        "focus": "1–7天",
        "direction": "逆向交易过度偏离",
        "best_for": "震荡市、急涨急跌后的修复",
        "risk": "单边趋势中逆势抄底或摸顶",
    },
)


@dataclass(frozen=True)
class HorizonForecast:
    key: str
    label: str
    days: int
    target: float
    low: float
    high: float
    expected_return: float
    up_probability: float
    confidence: float
    signal: str


@dataclass(frozen=True)
class SpotForecast:
    model_version: str
    inst_id: str
    symbol: str
    market_type: str
    current_price: float
    quote_time: str
    quote_source: str
    daily_change: float
    regime: str
    volatility: float
    forecasts: list[HorizonForecast]
    history: list[dict[str, float | str]]
    opportunity_score: int
    rebound_score: int
    decision: str
    buy_zone_low: float
    buy_zone_high: float
    trigger_price: float
    invalidation: float
    risk_reward: float
    factors: dict[str, str]
    market_context: dict
    data_quality: dict
    short_term_context: dict


class SpotForecastEngine:
    HORIZONS = FORECAST_HORIZONS

    def analyze(
        self,
        inst_id: str,
        candles: list[Candle],
        live_price: float | None = None,
        quote_time: datetime | None = None,
        market_context: dict | None = None,
        strategy_id: str = "adaptive",
    ) -> SpotForecast | None:
        raw_candles = list(candles)
        candles = self._canonical_candles(raw_candles)
        closed = candles[:-1] if len(candles) > 1 else candles
        if len(closed) < 90:
            return None
        raw_ordered = sorted(raw_candles, key=lambda item: item.ts)
        raw_closed = raw_ordered[:-1] if len(raw_ordered) > 1 else raw_ordered
        symbol = str(inst_id).split("-")[0].upper()
        data_quality = assess_short_term_data(
            raw_closed,
            as_of=quote_time,
            allow_weekends=symbol in {"AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"},
        )
        short_context = short_term_context(data_quality)
        closes = [item.close for item in closed]
        log_returns = [
            math.log(current / previous)
            for previous, current in zip(closes, closes[1:])
            if previous > 0 and current > 0
        ]
        model_price = closes[-1]
        current = live_price if live_price is not None and live_price > 0 else model_price
        daily_change = current / closes[-1] - 1
        # The last daily candle returned by the exchange is still forming. Use
        # its live ticker price as the newest observation instead of anchoring
        # trend and momentum to the previous UTC close.
        feature_closes = [*closes, current] if live_price is not None and live_price > 0 else closes
        volatility = pstdev(log_returns[-60:]) if len(log_returns) >= 2 else 0.0
        trend_7 = self._daily_slope(feature_closes[-7:])
        trend_14 = self._daily_slope(feature_closes[-14:])
        trend_30 = self._daily_slope(feature_closes[-30:])
        momentum_1 = current / feature_closes[-2] - 1
        momentum_3 = current / feature_closes[-4] - 1
        momentum_7 = current / feature_closes[-8] - 1
        momentum_14 = current / feature_closes[-15] - 1
        momentum_30 = current / feature_closes[-31] - 1
        stretch_20 = current / mean(feature_closes[-20:]) - 1
        regime = self._regime(trend_7, trend_30, volatility)
        forecasts = [
            self._forecast(
                key,
                label,
                days,
                current,
                volatility,
                trend_7,
                trend_14,
                trend_30,
                momentum_1,
                momentum_3,
                momentum_7,
                momentum_14,
                momentum_30,
                stretch_20,
                market_context or {},
                strategy_id,
            )
            for key, label, days in self.HORIZONS
        ]
        atr = self._atr(closed[-15:])
        recent_support = min(item.low for item in closed[-7:])
        recent_resistance = max(item.high for item in closed[-4:-1])
        buy_zone_low = max(recent_support, current - atr * 0.65)
        buy_zone_high = current + atr * 0.10
        trigger_price = max(current + atr * 0.10, recent_resistance)
        invalidation = min(recent_support - atr * 0.15, current - atr * 1.25)
        opportunity_score = score_opportunity(forecasts, volatility)
        # A gap-riddled or stale history may still produce mathematical
        # outputs, but it must never look like a trade-ready short-term edge.
        if not data_quality["actionable"]:
            opportunity_score = min(opportunity_score, 39)
        rebound_score, rebound_factors = self._rebound_profile(
            feature_closes,
            forecasts,
            volatility,
            market_context or {},
        )
        decision = decide_strategy(
            forecasts,
            opportunity_score,
            float((market_context or {}).get("market_environment_score", 0.0)),
        )
        decision = refine_rebound_decision(decision, forecasts, rebound_score)
        if not data_quality["actionable"]:
            decision = "历史数据质量不足，观望"
        positive = sum(1 for item in forecasts if item.expected_return > 0)
        week = next(item for item in forecasts if item.key == "1w")
        downside = max(current - invalidation, current * 0.005)
        risk_reward = max(0.0, week.target - current) / downside
        history = [
            {
                "date": item.ts.isoformat(),
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "volume": item.volume,
            }
            for item in closed[-FORECAST_HISTORY_LIMIT:]
        ]
        factors = {
            "trend": self._strength_label((trend_7 + trend_14 + trend_30) / 3 * 365),
            "momentum": self._strength_label((momentum_3 + momentum_7 + momentum_14) / 3),
            "volatility": "高" if volatility > 0.045 else "中" if volatility > 0.025 else "低",
            "agreement": f"{positive}/4 短周期偏多",
            "orderbook": self._factor_label((market_context or {}).get("orderbook_score", 0.0)),
            "funding": self._factor_label((market_context or {}).get("funding_score", 0.0)),
            "open_interest": self._factor_label((market_context or {}).get("open_interest_score", 0.0)),
            "volume_flow": self._factor_label((market_context or {}).get("volume_score", 0.0)),
            "macro": self._factor_label((market_context or {}).get("macro_score", 0.0)),
            "market_environment": str((market_context or {}).get("market_environment_label", "过渡震荡")),
            **rebound_factors,
        }
        return SpotForecast(
            model_version=FORECAST_MODEL_VERSION,
            inst_id=inst_id,
            symbol=inst_id.split("-")[0],
            market_type=(
                "股票映射行情"
                if inst_id.split("-")[0] in {"AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"}
                else "现货"
            ),
            current_price=current,
            quote_time=(quote_time or closed[-1].ts).isoformat(),
            quote_source="OKX ticker" if live_price is not None else "已收盘日K",
            daily_change=daily_change,
            regime=regime,
            volatility=volatility,
            forecasts=forecasts,
            history=history,
            opportunity_score=opportunity_score,
            rebound_score=rebound_score,
            decision=decision,
            buy_zone_low=buy_zone_low,
            buy_zone_high=buy_zone_high,
            trigger_price=trigger_price,
            invalidation=invalidation,
            risk_reward=risk_reward,
            factors=factors,
            market_context=market_context or {},
            data_quality=data_quality,
            short_term_context=short_context,
        )

    def analyze_suite(
        self,
        inst_id: str,
        candles: list[Candle],
        live_price: float | None = None,
        quote_time: datetime | None = None,
        market_context: dict | None = None,
    ) -> list[dict]:
        from dataclasses import asdict

        rows = []
        for profile in STRATEGY_CATALOG:
            forecast = self.analyze(
                inst_id,
                candles,
                live_price=live_price,
                quote_time=quote_time,
                market_context=market_context,
                strategy_id=str(profile["id"]),
            )
            if forecast is None:
                continue
            row = asdict(forecast)
            rows.append({
                "strategy": profile,
                "model_version": (
                    FORECAST_MODEL_VERSION
                    if profile["id"] == "adaptive"
                    else f"{FORECAST_MODEL_VERSION}:{profile['id']}"
                ),
                "inst_id": row["inst_id"],
                "current_price": row["current_price"],
                "volatility": row["volatility"],
                "market_context": row["market_context"],
                "data_quality": row["data_quality"],
                "short_term_context": row["short_term_context"],
                "forecasts": row["forecasts"],
                "opportunity_score": row["opportunity_score"],
                "rebound_score": row["rebound_score"],
                "decision": row["decision"],
                "factors": row["factors"],
            })
        return rows

    def _forecast(
        self,
        key: str,
        label: str,
        days: int,
        current: float,
        volatility: float,
        trend_7: float,
        trend_14: float,
        trend_30: float,
        momentum_1: float,
        momentum_3: float,
        momentum_7: float,
        momentum_14: float,
        momentum_30: float,
        stretch_20: float,
        market_context: dict,
        strategy_id: str,
    ) -> HorizonForecast:
        trend_rate = trend_7 * 0.45 + trend_14 * 0.35 + trend_30 * 0.20
        momentum_rate = self._momentum_rate(
            days,
            momentum_1,
            momentum_3,
            momentum_7,
            momentum_14,
            momentum_30,
        )
        trend_weight, momentum_weight = self._strategy_weights(strategy_id, days)
        if trend_rate * momentum_rate < 0:
            # Conflicting short-term evidence must reduce conviction rather than
            # letting the fastest feature dictate the full target.
            momentum_weight *= 0.50
            trend_weight = 1 - momentum_weight
        daily_edge = trend_rate * trend_weight + momentum_rate * momentum_weight
        directional_return = math.exp(
            self._clip(daily_edge * days, -0.35, 0.35)
        ) - 1
        stretch_sigma = max(volatility * math.sqrt(20), 0.015)
        overextension = self._clip(stretch_20 / stretch_sigma, -2.5, 2.5)
        if strategy_id == "mean_reversion":
            base_expected_return = directional_return * 0.20 - overextension * volatility * math.sqrt(days) * 0.42
        else:
            reversion_penalty = overextension * volatility * math.sqrt(days) * (
                0.12 if strategy_id == "trend" else 0.22
            )
            base_expected_return = directional_return - reversion_penalty
        factor_score, factor_delta = self._factor_adjustment(days, market_context)
        factor_multiplier = {
            "adaptive": 1.0,
            "trend": 0.70,
            "breakout": 1.25,
            "mean_reversion": 0.35,
        }.get(strategy_id, 1.0)
        raw_expected_return = base_expected_return + factor_delta * factor_multiplier
        natural_range = max(volatility * math.sqrt(days) * 1.9, 0.008 * math.sqrt(days))
        hard_limit = {1: 0.08, 3: 0.14, 7: 0.22, 14: 0.30}[days]
        positive_limit = min(hard_limit, natural_range)
        negative_limit = min(hard_limit * 0.90, natural_range)
        expected_return = self._soft_bound(
            raw_expected_return,
            negative_limit,
            positive_limit,
        )
        sigma = volatility * math.sqrt(days)
        agreement = self._agreement(trend_rate, momentum_rate)
        signal_to_noise = abs(expected_return) / max(sigma, 0.01)
        factor_alignment = (
            1.0
            if base_expected_return * factor_score > 0
            else -1.0
            if base_expected_return * factor_score < 0
            else 0.0
        )
        spread_penalty = min(
            0.08,
            max(0.0, float(market_context.get("spread_bps", 0.0)) - 3.0) / 250,
        )
        available = market_context.get("available", {})
        evidence_ratio = (
            sum(1 for value in available.values() if value) / len(available)
            if available
            else 0.0
        )
        confidence = self._clip(
            0.32
            + agreement * 0.14
            + min(signal_to_noise, 1.8) * 0.10
            + evidence_ratio * 0.08
            + factor_alignment * min(abs(factor_score), 1.0) * 0.05
            - spread_penalty,
            0.28,
            0.74,
        )
        z_score = expected_return / max(sigma, 0.01)
        up_probability = self._clip(1 / (1 + math.exp(-z_score)), 0.15, 0.85)
        target = current * (1 + expected_return)
        interval = current * max(sigma, 0.006 * math.sqrt(days)) * (
            1.20 + (1 - confidence) * 0.85
        )
        low = max(0.0, target - interval)
        high = target + interval
        signal_floor = {1: 0.006, 3: 0.012, 7: 0.020, 14: 0.030}[days]
        if expected_return > signal_floor and up_probability >= 0.55:
            signal = "偏多"
        elif expected_return < -signal_floor and up_probability <= 0.45:
            signal = "偏空"
        else:
            signal = "震荡"
        return HorizonForecast(
            key=key,
            label=label,
            days=days,
            target=target,
            low=low,
            high=high,
            expected_return=expected_return,
            up_probability=up_probability,
            confidence=confidence,
            signal=signal,
        )

    @staticmethod
    def _canonical_candles(candles: list[Candle]) -> list[Candle]:
        """Sort candles and discard duplicate/invalid timestamps before features."""
        return canonicalize_candles(candles)

    @staticmethod
    def _daily_slope(values: list[float]) -> float:
        logs = [math.log(value) for value in values if value > 0]
        if len(logs) < 2:
            return 0.0
        x_mean = (len(logs) - 1) / 2
        y_mean = mean(logs)
        denominator = sum((index - x_mean) ** 2 for index in range(len(logs)))
        if denominator == 0:
            return 0.0
        slope = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(logs)) / denominator
        return slope

    @staticmethod
    def _momentum_blend(
        days: int,
        momentum_7: float,
        momentum_30: float,
        momentum_90: float,
    ) -> float:
        """Compatibility helper for callers of the pre-short-horizon API.

        The production model no longer emits 1/3/6-month forecasts, but a few
        notebooks and stored calibration jobs still import this pure helper.
        Keep its old bounded blend available without using it in live signals.
        """
        if days <= 7:
            return momentum_7 * 0.65 + momentum_30 * 0.25 + momentum_90 * 0.10
        if days <= 30:
            return momentum_7 * 0.25 + momentum_30 * 0.55 + momentum_90 * 0.20
        if days <= 90:
            return momentum_7 * 0.10 + momentum_30 * 0.35 + momentum_90 * 0.55
        return momentum_30 * 0.20 + momentum_90 * 0.80

    @staticmethod
    def _momentum_rate(
        days: int,
        momentum_1: float,
        momentum_3: float,
        momentum_7: float,
        momentum_14: float,
        momentum_30: float,
    ) -> float:
        rates = (
            math.log1p(max(momentum_1, -0.95)),
            math.log1p(max(momentum_3, -0.95)) / 3,
            math.log1p(max(momentum_7, -0.95)) / 7,
            math.log1p(max(momentum_14, -0.95)) / 14,
            math.log1p(max(momentum_30, -0.95)) / 30,
        )
        if days <= 1:
            weights = (0.42, 0.28, 0.18, 0.08, 0.04)
        elif days <= 3:
            weights = (0.18, 0.34, 0.28, 0.14, 0.06)
        elif days <= 7:
            weights = (0.08, 0.22, 0.34, 0.25, 0.11)
        else:
            weights = (0.04, 0.12, 0.25, 0.38, 0.21)
        return sum(weight * rate for weight, rate in zip(weights, rates))

    @staticmethod
    def _horizon_weights(days: int) -> tuple[float, float]:
        if days <= 1:
            return 0.35, 0.65
        if days <= 3:
            return 0.45, 0.55
        if days <= 7:
            return 0.58, 0.42
        return 0.68, 0.32

    @staticmethod
    def _strategy_weights(strategy_id: str, days: int) -> tuple[float, float]:
        if strategy_id == "trend":
            return (0.84, 0.16) if days >= 7 else (0.72, 0.28)
        if strategy_id == "breakout":
            return (0.30, 0.70) if days <= 3 else (0.46, 0.54)
        return SpotForecastEngine._horizon_weights(days)

    @staticmethod
    def _soft_bound(value: float, negative_limit: float, positive_limit: float) -> float:
        """Preserve cross-sectional ranking without hard-clipping forecasts."""
        limit = positive_limit if value >= 0 else negative_limit
        return limit * math.tanh(value / limit)

    @staticmethod
    def _factor_adjustment(days: int, context: dict) -> tuple[float, float]:
        if days <= 1:
            weights = (0.38, 0.10, 0.06, 0.22, 0.05, 0.19)
            max_delta = 0.008
        elif days <= 3:
            weights = (0.30, 0.11, 0.10, 0.22, 0.07, 0.20)
            max_delta = 0.015
        elif days <= 7:
            weights = (0.20, 0.13, 0.13, 0.21, 0.11, 0.22)
            max_delta = 0.025
        else:
            weights = (0.10, 0.13, 0.17, 0.18, 0.17, 0.25)
            max_delta = 0.035
        values = (
            float(context.get("orderbook_score", 0.0)),
            float(context.get("funding_score", 0.0)),
            float(context.get("open_interest_score", 0.0)),
            float(context.get("volume_score", 0.0)),
            float(context.get("macro_score", 0.0)),
            float(context.get("market_environment_score", 0.0)),
        )
        score = sum(weight * max(-1.0, min(1.0, value)) for weight, value in zip(weights, values))
        return score, score * max_delta

    @classmethod
    def _rebound_profile(
        cls,
        closes: list[float],
        forecasts: list[HorizonForecast],
        volatility: float,
        market_context: dict,
    ) -> tuple[int, dict[str, str]]:
        current = closes[-1]
        window_30 = closes[-30:]
        window_60 = closes[-60:]
        high_60 = max(window_60)
        low_30 = min(window_30)
        ma20 = mean(closes[-20:])
        momentum_7 = current / closes[-8] - 1 if len(closes) >= 8 else 0.0
        discount_from_high = max(0.0, high_60 / current - 1)
        ma_discount = max(0.0, ma20 / current - 1)
        distance_from_low = current / low_30 - 1 if low_30 > 0 else 0.0
        by_key = {item.key: item for item in forecasts}
        short = by_key.get("3d")
        week = by_key.get("1w")
        short_return = short.expected_return if short else 0.0
        week_return = week.expected_return if week else 0.0
        short_probability = short.up_probability if short else 0.5
        volume_score = max(-1.0, min(1.0, float(market_context.get("volume_score", 0.0))))
        environment_score = max(
            -1.0,
            min(1.0, float(market_context.get("market_environment_score", 0.0))),
        )
        pullback_quality = (
            min(discount_from_high / 0.18, 1.0) * 28
            + min(ma_discount / 0.08, 1.0) * 8
        )
        support_quality = (
            min(max(distance_from_low - 0.015, 0.0) / 0.08, 1.0) * 18
            + min(max(momentum_7, 0.0) / 0.04, 1.0) * 12
        )
        forecast_quality = (
            cls._clip((short_return + 0.03) / 0.08, 0.0, 1.0) * 11
            + cls._clip((short_probability - 0.40) / 0.24, 0.0, 1.0) * 7
            + cls._clip((week_return + 0.05) / 0.12, 0.0, 1.0) * 5
        )
        context_quality = 12 + volume_score * 7 + environment_score * 6
        breakdown_penalty = 0.0
        if distance_from_low < 0.018:
            breakdown_penalty += 22
        if short_return < 0:
            breakdown_penalty += min(20.0, abs(short_return) / 0.06 * 20)
        if week_return < -0.06:
            breakdown_penalty += min(10.0, abs(week_return + 0.06) / 0.10 * 10)
        breakdown_penalty += min(10.0, max(0.0, volatility - 0.045) * 180)
        raw_score = (
            pullback_quality
            + support_quality
            + forecast_quality
            + context_quality
            - breakdown_penalty
        )
        score = round(cls._clip(raw_score, 0.0, 100.0))
        breakdown_risk = distance_from_low < 0.018 or short_return < 0
        if score >= 65:
            rebound_label = "强反弹候选"
        elif score >= 50:
            rebound_label = "等待企稳"
        elif score <= 35 and breakdown_risk:
            rebound_label = "破位风险"
        else:
            rebound_label = "普通回撤"
        support_label = (
            f"低点上方 {distance_from_low * 100:.1f}%"
            if distance_from_low >= 0.018
            else "贴近30日低点"
        )
        return score, {
            "rebound": rebound_label,
            "discount": f"60日高点折价 {discount_from_high * 100:.1f}%",
            "stabilization": support_label,
        }

    @staticmethod
    def _agreement(trend: float, momentum: float) -> float:
        if trend == 0 or momentum == 0:
            return 0.25
        return 1.0 if trend * momentum > 0 else 0.0

    @staticmethod
    def _regime(trend_7: float, trend_30: float, volatility: float) -> str:
        if volatility > 0.055:
            return "高波动"
        if trend_7 > 0.0008 and trend_30 > 0.0004:
            return "短线上升"
        if trend_7 < -0.0008 and trend_30 < -0.0004:
            return "短线下降"
        return "短线震荡"

    @staticmethod
    def _strength_label(value: float) -> str:
        if value > 0.08:
            return "偏强"
        if value < -0.08:
            return "偏弱"
        return "中性"

    @staticmethod
    def _factor_label(value: float) -> str:
        if value >= 0.18:
            return "利多"
        if value <= -0.18:
            return "利空"
        return "中性"

    @staticmethod
    def _atr(candles: list[Candle]) -> float:
        ranges = []
        for previous, current in zip(candles, candles[1:]):
            ranges.append(
                max(
                    current.high - current.low,
                    abs(current.high - previous.close),
                    abs(current.low - previous.close),
                )
            )
        return mean(ranges) if ranges else 0.0

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))


def score_opportunity(forecasts: list[HorizonForecast] | list[dict], volatility: float) -> int:
    by_key = {
        str(_value(item, "key")): item
        for item in forecasts
    }
    keys = ("1d", "3d", "1w", "2w")
    weights = (0.12, 0.38, 0.35, 0.15)
    rows = [by_key.get(key) for key in keys]
    if any(item is None for item in rows):
        return 0
    probability = sum(
        float(_value(item, "up_probability")) * weight
        for item, weight in zip(rows, weights)
    )
    confidence = sum(
        float(_value(item, "confidence")) * weight
        for item, weight in zip(rows, weights)
    )
    risk_adjusted_returns = [
        float(_value(item, "expected_return"))
        / max(volatility * math.sqrt(float(_value(item, "days") or days)), 0.01)
        for item, days in zip(rows, (1, 3, 7, 14))
    ]
    return_quality = sum(
        self_weight * (0.5 + 0.5 * math.tanh(value))
        for self_weight, value in zip(weights, risk_adjusted_returns)
    )
    agreement = sum(
        1 for item in rows if float(_value(item, "expected_return")) > 0
    ) / len(rows)
    core_agreement = (
        float(_value(by_key["3d"], "expected_return"))
        * float(_value(by_key["1w"], "expected_return"))
        > 0
    )
    volatility_penalty = min(10.0, max(0.0, volatility - 0.025) * 170)
    score = (
        probability * 0.32
        + return_quality * 0.26
        + confidence * 0.22
        + agreement * 0.12
        + (0.08 if core_agreement else 0.0)
    ) * 100 - volatility_penalty
    return round(max(0.0, min(100.0, score)))


def decide_strategy(
    forecasts: list[HorizonForecast] | list[dict],
    score: int,
    market_environment_score: float,
) -> str:
    by_key = {str(_value(item, "key")): item for item in forecasts}
    rows = [by_key.get(key) for key in ("1d", "3d", "1w", "2w")]
    if any(item is None for item in rows):
        return "数据不足"
    positive = sum(
        1 for item in rows if float(_value(item, "expected_return")) > 0
    )
    short = by_key["3d"]
    week = by_key["1w"]
    weighted_confidence = sum(
        float(_value(item, "confidence")) * weight
        for item, weight in zip(rows, (0.12, 0.38, 0.35, 0.15))
    )
    buy_ready = (
        score >= 72
        and positive >= 3
        and float(_value(short, "expected_return")) > 0
        and float(_value(week, "expected_return")) > 0
        and float(_value(short, "up_probability")) >= 0.57
        and float(_value(week, "up_probability")) >= 0.55
        and weighted_confidence >= 0.48
    )
    if buy_ready:
        return (
            "风险收缩，等待确认"
            if market_environment_score <= -0.35
            else "短线条件成立"
        )
    if score >= 60 and positive >= 3:
        return "等待短线触发"
    if score < 45 or positive <= 1:
        return "短线回避"
    return "方向冲突，观望"


def refine_rebound_decision(
    decision: str,
    forecasts: list[HorizonForecast] | list[dict],
    rebound_score: int,
) -> str:
    by_key = {str(_value(item, "key")): item for item in forecasts}
    short = by_key.get("3d")
    short_return = float(_value(short, "expected_return")) if short else 0.0
    short_probability = float(_value(short, "up_probability")) if short else 0.5
    if rebound_score <= 35 and (short_return < 0 or short_probability <= 0.43):
        return "破位风险，短线回避"
    if (
        rebound_score >= 65
        and short_return > 0
        and short_probability >= 0.54
    ):
        return "超跌修复候选"
    if rebound_score >= 55 and decision in {"短线回避", "方向冲突，观望"}:
        return "等待企稳确认"
    return decision


def _value(item: HorizonForecast | dict, key: str):
    return item.get(key) if isinstance(item, dict) else getattr(item, key)
