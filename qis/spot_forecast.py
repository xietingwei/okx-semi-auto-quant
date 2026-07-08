from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import mean, pstdev

from qis.models import Candle


FORECAST_MODEL_VERSION = "global_regime_context_v7"
FORECAST_HISTORY_LIMIT = 200

STRATEGY_CATALOG = (
    {
        "id": "adaptive",
        "name": "综合自适应",
        "focus": "全周期平衡",
        "direction": "顺势为主，兼顾拥挤与大环境",
        "best_for": "方向尚未完全统一、需要综合判断的行情",
        "risk": "极端事件下各类因子可能同时失效",
    },
    {
        "id": "trend",
        "name": "趋势跟随",
        "focus": "1月–6月",
        "direction": "沿30/90日主趋势持有",
        "best_for": "单边趋势和回撤后续涨",
        "risk": "震荡市容易反复止损",
    },
    {
        "id": "breakout",
        "name": "突破确认",
        "focus": "1天–1月",
        "direction": "跟随放量突破与盘口确认",
        "best_for": "动量启动、量价和持仓同步扩张",
        "risk": "假突破和流动性骤降",
    },
    {
        "id": "mean_reversion",
        "name": "均值回归",
        "focus": "1天–1月",
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
    invalidation: float
    factors: dict[str, str]
    market_context: dict


class SpotForecastEngine:
    HORIZONS = (
        ("1d", "1天", 1),
        ("1w", "1周", 7),
        ("1m", "1月", 30),
        ("3m", "3月", 90),
        ("6m", "6月", 180),
    )

    def analyze(
        self,
        inst_id: str,
        candles: list[Candle],
        live_price: float | None = None,
        quote_time: datetime | None = None,
        market_context: dict | None = None,
        strategy_id: str = "adaptive",
    ) -> SpotForecast | None:
        closed = candles[:-1] if len(candles) > 1 else candles
        if len(closed) < 90:
            return None
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
        trend_30 = self._annualized_slope(feature_closes[-30:])
        trend_90 = self._annualized_slope(feature_closes[-90:])
        momentum_7 = current / feature_closes[-8] - 1
        momentum_30 = current / feature_closes[-31] - 1
        momentum_90 = current / feature_closes[-91] - 1
        regime = self._regime(trend_30, trend_90, volatility)
        forecasts = [
            self._forecast(
                key,
                label,
                days,
                current,
                volatility,
                trend_30,
                trend_90,
                momentum_7,
                momentum_30,
                momentum_90,
                market_context or {},
                strategy_id,
            )
            for key, label, days in self.HORIZONS
        ]
        atr = self._atr(closed[-15:])
        buy_zone_low = current - atr * 0.75
        buy_zone_high = current + atr * 0.15
        invalidation = current - atr * 1.8
        opportunity_score = score_opportunity(forecasts, volatility)
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
        positive = sum(1 for item in forecasts if item.expected_return > 0)
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
            "trend": self._strength_label((trend_30 + trend_90) / 2),
            "momentum": self._strength_label((momentum_7 + momentum_30) / 2),
            "volatility": "高" if volatility > 0.045 else "中" if volatility > 0.025 else "低",
            "agreement": f"{positive}/5 周期偏多",
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
            invalidation=invalidation,
            factors=factors,
            market_context=market_context or {},
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
        trend_30: float,
        trend_90: float,
        momentum_7: float,
        momentum_30: float,
        momentum_90: float,
        market_context: dict,
        strategy_id: str,
    ) -> HorizonForecast:
        horizon_years = days / 365
        short_weight = max(0.15, 1 - days / 210)
        annual_trend = trend_30 * short_weight + trend_90 * (1 - short_weight)
        momentum = self._momentum_blend(days, momentum_7, momentum_30, momentum_90)
        trend_return = math.exp(
            self._clip(annual_trend * horizon_years, -0.55, 0.75)
        ) - 1
        mean_reversion_penalty = -0.18 * momentum if abs(momentum) > volatility * math.sqrt(max(days, 1)) * 1.8 else 0.0
        trend_weight, momentum_weight = self._strategy_weights(strategy_id, days)
        if annual_trend * momentum < 0:
            # Historical evaluation shows weak 1w/1m direction. When trend and
            # momentum disagree, do not let short-lived momentum dictate targets.
            momentum_weight *= 0.45
            trend_weight = 1 - momentum_weight
        if strategy_id == "mean_reversion":
            reversion_strength = min(
                0.72,
                0.28 + abs(momentum) / max(volatility * math.sqrt(max(days, 1)), 0.01) * 0.08,
            )
            horizon_decay = 1.0 if days <= 30 else 0.45
            base_expected_return = (
                trend_return * 0.24
                - momentum * reversion_strength * horizon_decay
            )
        else:
            base_expected_return = (
                trend_return * trend_weight
                + momentum * momentum_weight
                + mean_reversion_penalty
            )
        factor_score, factor_delta = self._factor_adjustment(days, market_context)
        factor_multiplier = {
            "adaptive": 1.0,
            "trend": 0.70,
            "breakout": 1.25,
            "mean_reversion": 0.35,
        }.get(strategy_id, 1.0)
        raw_expected_return = base_expected_return + factor_delta * factor_multiplier
        positive_limit = 0.38 if strategy_id == "mean_reversion" else 0.45
        negative_limit = 0.30 if strategy_id == "mean_reversion" else 0.35
        expected_return = self._soft_bound(
            raw_expected_return,
            negative_limit,
            positive_limit,
        )
        sigma = volatility * math.sqrt(days)
        agreement = self._agreement(annual_trend, momentum)
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
        confidence = self._clip(
            0.38
            + agreement * 0.18
            + min(signal_to_noise, 2) * 0.13
            + factor_alignment * min(abs(factor_score), 1.0) * 0.06
            - spread_penalty,
            0.32,
            0.82,
        )
        if strategy_id == "mean_reversion" and days > 30:
            confidence = max(0.32, confidence - 0.12)
        z_score = expected_return / max(sigma, 0.01)
        up_probability = self._clip(1 / (1 + math.exp(-z_score * 1.15)), 0.12, 0.88)
        target = current * (1 + expected_return)
        interval = current * sigma * (1.15 + (1 - confidence) * 0.8)
        low = max(0.0, target - interval)
        high = target + interval
        if expected_return > 0.025 and up_probability >= 0.58:
            signal = "偏多"
        elif expected_return < -0.025 and up_probability <= 0.42:
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
    def _annualized_slope(values: list[float]) -> float:
        logs = [math.log(value) for value in values if value > 0]
        if len(logs) < 2:
            return 0.0
        x_mean = (len(logs) - 1) / 2
        y_mean = mean(logs)
        denominator = sum((index - x_mean) ** 2 for index in range(len(logs)))
        if denominator == 0:
            return 0.0
        slope = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(logs)) / denominator
        return slope * 365

    @staticmethod
    def _momentum_blend(days: int, momentum_7: float, momentum_30: float, momentum_90: float) -> float:
        if days <= 7:
            return momentum_7 * 0.65 + momentum_30 * 0.25 + momentum_90 * 0.10
        if days <= 30:
            return momentum_7 * 0.25 + momentum_30 * 0.55 + momentum_90 * 0.20
        if days <= 90:
            return momentum_7 * 0.10 + momentum_30 * 0.35 + momentum_90 * 0.55
        # Long-horizon momentum is a regime feature, not a return multiplier.
        return momentum_30 * 0.20 + momentum_90 * 0.80

    @staticmethod
    def _horizon_weights(days: int) -> tuple[float, float]:
        if days <= 7:
            return 0.72, 0.28
        if days <= 30:
            return 0.70, 0.30
        if days <= 90:
            return 0.66, 0.34
        return 0.72, 0.28

    @staticmethod
    def _strategy_weights(strategy_id: str, days: int) -> tuple[float, float]:
        if strategy_id == "trend":
            return (0.88, 0.12) if days >= 30 else (0.80, 0.20)
        if strategy_id == "breakout":
            return (0.42, 0.58) if days <= 30 else (0.58, 0.42)
        return SpotForecastEngine._horizon_weights(days)

    @staticmethod
    def _soft_bound(value: float, negative_limit: float, positive_limit: float) -> float:
        """Preserve cross-sectional ranking without hard-clipping forecasts."""
        limit = positive_limit if value >= 0 else negative_limit
        return limit * math.tanh(value / limit)

    @staticmethod
    def _factor_adjustment(days: int, context: dict) -> tuple[float, float]:
        if days <= 7:
            weights = (0.30, 0.12, 0.08, 0.18, 0.08, 0.24)
            max_delta = 0.035
        elif days <= 30:
            weights = (0.12, 0.15, 0.15, 0.18, 0.15, 0.25)
            max_delta = 0.050
        elif days <= 90:
            weights = (0.04, 0.14, 0.20, 0.17, 0.18, 0.27)
            max_delta = 0.060
        else:
            weights = (0.02, 0.12, 0.20, 0.12, 0.24, 0.30)
            max_delta = 0.055
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
        month = by_key.get("1m")
        quarter = by_key.get("3m")
        month_return = month.expected_return if month else 0.0
        quarter_return = quarter.expected_return if quarter else 0.0
        month_probability = month.up_probability if month else 0.5
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
            cls._clip((month_return + 0.08) / 0.18, 0.0, 1.0) * 10
            + cls._clip((month_probability - 0.38) / 0.30, 0.0, 1.0) * 6
            + cls._clip((quarter_return + 0.10) / 0.24, 0.0, 1.0) * 6
        )
        context_quality = 12 + volume_score * 7 + environment_score * 6
        breakdown_penalty = 0.0
        if distance_from_low < 0.018:
            breakdown_penalty += 22
        if month_return < 0:
            breakdown_penalty += min(20.0, abs(month_return) / 0.15 * 20)
        if quarter_return < -0.12:
            breakdown_penalty += min(10.0, abs(quarter_return + 0.12) / 0.18 * 10)
        breakdown_penalty += min(10.0, max(0.0, volatility - 0.045) * 180)
        raw_score = (
            pullback_quality
            + support_quality
            + forecast_quality
            + context_quality
            - breakdown_penalty
        )
        score = round(cls._clip(raw_score, 0.0, 100.0))
        breakdown_risk = distance_from_low < 0.018 or month_return < 0
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
    def _regime(trend_30: float, trend_90: float, volatility: float) -> str:
        if volatility > 0.055:
            return "高波动"
        if trend_30 > 0.08 and trend_90 > 0.05:
            return "上升趋势"
        if trend_30 < -0.08 and trend_90 < -0.05:
            return "下降趋势"
        return "区间震荡"

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
    rows = [by_key.get(key) for key in ("1w", "1m", "3m")]
    if any(item is None for item in rows):
        return 0
    week, month, quarter = rows
    probability = (
        float(_value(week, "up_probability")) * 0.25
        + float(_value(month, "up_probability")) * 0.45
        + float(_value(quarter, "up_probability")) * 0.30
    )
    expected_return = (
        float(_value(week, "expected_return")) * 0.20
        + float(_value(month, "expected_return")) * 0.45
        + float(_value(quarter, "expected_return")) * 0.35
    )
    confidence = (
        float(_value(week, "confidence")) * 0.25
        + float(_value(month, "confidence")) * 0.45
        + float(_value(quarter, "confidence")) * 0.30
    )
    agreement = sum(
        1 for item in rows if float(_value(item, "expected_return")) > 0
    ) / 3
    return_quality = max(0.0, min(1.0, (expected_return + 0.02) / 0.18))
    volatility_penalty = min(12.0, max(0.0, volatility) * 120)
    score = (
        probability * 0.38
        + return_quality * 0.27
        + confidence * 0.20
        + agreement * 0.15
    ) * 100 - volatility_penalty
    return round(max(0.0, min(100.0, score)))


def decide_strategy(
    forecasts: list[HorizonForecast] | list[dict],
    score: int,
    market_environment_score: float,
) -> str:
    by_key = {str(_value(item, "key")): item for item in forecasts}
    rows = [by_key.get(key) for key in ("1w", "1m", "3m")]
    if any(item is None for item in rows):
        return "数据不足"
    positive = sum(
        1 for item in rows if float(_value(item, "expected_return")) > 0
    )
    month = by_key["1m"]
    weighted_confidence = sum(
        float(_value(item, "confidence")) * weight
        for item, weight in zip(rows, (0.25, 0.45, 0.30))
    )
    buy_ready = (
        score >= 70
        and positive == 3
        and float(_value(month, "up_probability")) >= 0.60
        and weighted_confidence >= 0.55
    )
    if buy_ready:
        return (
            "逆势等待确认"
            if market_environment_score <= -0.35
            else "分批关注买入"
        )
    if score >= 60 and positive >= 2:
        return "观察等待触发"
    if score < 45 or positive <= 1:
        return "等待趋势企稳"
    return "中性观察"


def refine_rebound_decision(
    decision: str,
    forecasts: list[HorizonForecast] | list[dict],
    rebound_score: int,
) -> str:
    by_key = {str(_value(item, "key")): item for item in forecasts}
    month = by_key.get("1m")
    month_return = float(_value(month, "expected_return")) if month else 0.0
    month_probability = float(_value(month, "up_probability")) if month else 0.5
    if rebound_score <= 35 and (month_return < 0 or month_probability <= 0.42):
        return "破位风险，暂不抄底"
    if (
        rebound_score >= 65
        and month_return > 0
        and month_probability >= 0.54
    ):
        return "跌后反弹候选"
    if rebound_score >= 55 and decision in {"等待趋势企稳", "中性观察"}:
        return "等待企稳确认"
    return decision


def _value(item: HorizonForecast | dict, key: str):
    return item.get(key) if isinstance(item, dict) else getattr(item, key)
