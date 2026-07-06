from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import urllib.parse
import urllib.request
from typing import Any


@dataclass(frozen=True)
class NewsItem:
    published_at: datetime
    title: str
    source: str
    url: str = ""
    sentiment: str = "neutral"


class YahooNewsClient:
    BASE_URL = "https://query1.finance.yahoo.com/v1/finance/search"

    def latest_news(self, symbol: str, limit: int = 12) -> list[NewsItem]:
        query = urllib.parse.urlencode(
            {
                "q": _news_query_symbol(symbol),
                "quotesCount": "0",
                "newsCount": str(max(1, min(limit, 20))),
            }
        )
        request = urllib.request.Request(
            f"{self.BASE_URL}?{query}",
            headers={"User-Agent": "qis-deep-analysis/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode())
        except Exception:
            return []
        rows = []
        for item in payload.get("news", []) or []:
            try:
                ts = datetime.fromtimestamp(
                    int(item.get("providerPublishTime") or 0),
                    tz=timezone.utc,
                )
            except (TypeError, ValueError, OSError):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            rows.append(
                NewsItem(
                    published_at=ts,
                    title=title,
                    source=str(item.get("publisher") or "Yahoo Finance"),
                    url=str(item.get("link") or ""),
                    sentiment=_sentiment(title),
                )
            )
        return rows


CORE_MIN_SAMPLES = 8
CORE_MIN_SUCCESS_RATE = 0.60
WATCH_MIN_SAMPLES = 5
WATCH_MIN_SUCCESS_RATE = 0.54
DEEP_ANALYSIS_MAX_DAYS = 180


class DeepAnalysisEngine:
    def analyze(
        self,
        forecast: dict[str, Any],
        *,
        news: list[NewsItem] | None = None,
        max_days: int = DEEP_ANALYSIS_MAX_DAYS,
    ) -> dict[str, Any]:
        candles = _history(forecast)
        if len(candles) < 35:
            raise ValueError("深度分析至少需要 35 根日 K")
        max_days = max(20, min(max_days, DEEP_ANALYSIS_MAX_DAYS))
        start = max(1, len(candles) - max_days)
        news_by_day = _news_by_day(news or [])
        daily = [
            self._daily_row(candles, index, news_by_day)
            for index in range(start, len(candles))
        ]
        patterns = self._super_brain(daily)
        scenarios = self._scenarios(daily[-1], patterns, forecast)
        core_patterns = [
            item for item in patterns if item.get("usable_for_projection")
        ]
        core_tested = sum(int(item.get("tested_count") or 0) for item in core_patterns)
        core_validated = sum(
            int(item.get("validated_count") or 0) for item in core_patterns
        )
        current_pattern = next(
            (
                item
                for item in patterns
                if item["pattern_id"] == daily[-1]["pattern"]["id"]
            ),
            None,
        )
        validated = [
            item
            for day in daily
            for item in day["hypotheses"]
            if item["validation"]["status"] == "confirmed"
        ]
        tested = [
            item
            for day in daily
            for item in day["hypotheses"]
            if item["validation"]["status"] != "pending"
        ]
        return {
            "inst_id": forecast.get("inst_id"),
            "symbol": forecast.get("symbol"),
            "market_type": forecast.get("market_type"),
            "data_source": forecast.get("data_source") or forecast.get("quote_source"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "range_days": len(daily),
            "shadow_brain": forecast.get("shadow_brain") or {},
            "quality_gate": {
                "daily_coverage": len(daily),
                "tested_hypotheses": len(tested),
                "validated_hypotheses": len(validated),
                "validation_rate": round(len(validated) / len(tested), 4) if tested else 0.0,
                "verified_patterns": len(core_patterns),
                "core_patterns": len(core_patterns),
                "core_tested_hypotheses": core_tested,
                "core_validated_hypotheses": core_validated,
                "core_validation_rate": (
                    round(core_validated / core_tested, 4)
                    if core_tested
                    else 0.0
                ),
                "projection_ready": bool(
                    current_pattern
                    and current_pattern.get("usable_for_projection")
                ),
                "external_news_items": len(news or []),
            },
            "scenarios": scenarios,
            "super_brain": patterns,
            "daily": list(reversed(daily)),
        }

    def _daily_row(
        self,
        candles: list[dict[str, Any]],
        index: int,
        news_by_day: dict[str, list[NewsItem]],
    ) -> dict[str, Any]:
        item = candles[index]
        prev = candles[index - 1]
        prior = candles[:index]
        facts = _facts(item, prev, prior)
        pattern = _pattern(facts)
        matched_news = news_by_day.get(str(item["date"])[:10], [])
        hypotheses = _hypotheses(pattern, facts, matched_news)
        for hypothesis in hypotheses:
            hypothesis["validation"] = _validate_hypothesis(
                candles,
                index,
                hypothesis["direction"],
            )
        return {
            "date": str(item["date"])[:10],
            "close": item["close"],
            "return_pct": facts["return_pct"],
            "pattern": pattern,
            "facts": facts,
            "events": [
                {
                    "title": event.title,
                    "source": event.source,
                    "sentiment": event.sentiment,
                    "url": event.url,
                }
                for event in matched_news
            ],
            "hypotheses": hypotheses,
        }

    def _super_brain(self, daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for day in daily:
            grouped[day["pattern"]["id"]].append(day)
        rows = []
        for pattern_id, days in grouped.items():
            tested = []
            for day in days:
                validation = day["hypotheses"][0]["validation"] if day["hypotheses"] else {}
                if validation.get("status") != "pending":
                    tested.append((day, validation))
            if not tested:
                continue
            confirmed = [
                row
                for row in tested
                if row[1].get("status") == "confirmed"
            ]
            success_rate = len(confirmed) / len(tested)
            returns = [float(row[1].get("return_5d") or 0) for row in tested]
            prototype = days[-1]["pattern"]
            quality_tier = _pattern_quality_tier(len(tested), success_rate)
            rows.append(
                {
                    "pattern_id": pattern_id,
                    "name": prototype["name"],
                    "direction": prototype["direction"],
                    "samples": len(tested),
                    "tested_count": len(tested),
                    "validated_count": len(confirmed),
                    "success_rate": round(success_rate, 4),
                    "avg_5d_return": round(sum(returns) / len(returns), 4),
                    "max_drawdown_median": round(_median([
                        float(row[1].get("max_drawdown_5d") or 0)
                        for row in tested
                    ]), 4),
                    "evidence": prototype["evidence"],
                    "invalidation": prototype["invalidation"],
                    "last_seen": days[-1]["date"],
                    "quality_tier": quality_tier,
                    "usable_for_projection": quality_tier == "core",
                    "verdict": _pattern_verdict(len(tested), success_rate),
                }
            )
        rows.sort(
            key=lambda row: (
                row.get("usable_for_projection", False),
                row["success_rate"],
                abs(row["avg_5d_return"]),
            ),
            reverse=True,
        )
        return rows

    def _scenarios(
        self,
        latest: dict[str, Any],
        patterns: list[dict[str, Any]],
        forecast: dict[str, Any],
    ) -> list[dict[str, Any]]:
        current_pattern = next(
            (item for item in patterns if item["pattern_id"] == latest["pattern"]["id"]),
            None,
        )
        one_week = next(
            (
                item
                for item in forecast.get("forecasts", [])
                if item.get("key") == "1w"
            ),
            {},
        )
        expected = float(one_week.get("expected_return") or 0)
        if not current_pattern or not current_pattern.get("usable_for_projection"):
            return _low_confidence_scenarios(latest, current_pattern, expected)
        if current_pattern:
            success = float(current_pattern["success_rate"])
            pattern_return = float(current_pattern["avg_5d_return"])
        else:
            success = 0.5
            pattern_return = 0.0
        bullish = max(0.18, min(0.62, 0.30 + success * 0.25 + max(expected, 0) * 1.5))
        risk = max(0.16, min(0.52, 0.28 + max(-expected, 0) * 1.8 + max(-pattern_return, 0) * 2.0))
        base = max(0.18, 1 - bullish - risk)
        total = bullish + base + risk
        return [
            {
                "name": "基础情景",
                "probability": round(base / total, 4),
                "direction": "震荡偏强" if expected >= 0 else "震荡偏弱",
                "reason": "当前结构与已验证模式共振不强，优先等待下一根日 K 确认。",
                "trigger": latest["pattern"]["invalidation"],
            },
            {
                "name": "乐观情景",
                "probability": round(bullish / total, 4),
                "direction": "延续上行",
                "reason": (
                    f"当前模式 {latest['pattern']['name']} "
                    f"历史验证成功率约 {success * 100:.0f}%。"
                ),
                "trigger": "放量收盘高于最近 20 日高点，且次日不跌回突破位。",
            },
            {
                "name": "风险情景",
                "probability": round(risk / total, 4),
                "direction": "冲高回落 / 下探",
                "reason": "若价格跌破失效条件，说明当前推测被市场否定。",
                "trigger": latest["pattern"]["invalidation"],
            },
        ]


def rank_deep_analyses(
    forecasts: list[dict[str, Any]],
    *,
    max_days: int = DEEP_ANALYSIS_MAX_DAYS,
) -> dict[str, Any]:
    days = max(20, min(max_days, DEEP_ANALYSIS_MAX_DAYS))
    engine = DeepAnalysisEngine()
    ranked = []
    skipped = []
    for forecast in forecasts:
        inst_id = str(forecast.get("inst_id") or "")
        try:
            analysis = engine.analyze(forecast, news=[], max_days=days)
        except ValueError as exc:
            skipped.append({"inst_id": inst_id, "error": str(exc)})
            continue
        ranked.append(_rank_row(forecast, analysis))
    ranked.sort(key=_rank_sort_key, reverse=True)
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "total": len(ranked),
        "ranked": ranked,
        "skipped": skipped,
    }


def _rank_row(forecast: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    gate = analysis.get("quality_gate") or {}
    scenario = (analysis.get("scenarios") or [{}])[0]
    latest = (analysis.get("daily") or [{}])[0]
    pattern = latest.get("pattern") or {}
    brain = analysis.get("super_brain") or []
    current_brain = next(
        (item for item in brain if item.get("pattern_id") == pattern.get("id")),
        {},
    )
    core_patterns = int(gate.get("core_patterns") or gate.get("verified_patterns") or 0)
    projection_ready = bool(gate.get("projection_ready"))
    if projection_ready:
        status = "核心可推演"
    elif core_patterns:
        status = "有核心模式待触发"
    else:
        status = "低可信观察"
    return {
        "rank": 0,
        "inst_id": analysis.get("inst_id") or forecast.get("inst_id"),
        "symbol": analysis.get("symbol") or forecast.get("symbol"),
        "market_type": analysis.get("market_type") or forecast.get("market_type"),
        "data_source": analysis.get("data_source")
        or forecast.get("data_source")
        or forecast.get("quote_source"),
        "current_price": forecast.get("current_price"),
        "rank_score": round(_rank_score(gate), 2),
        "status": status,
        "projection_ready": projection_ready,
        "core_patterns": core_patterns,
        "core_validation_rate": float(gate.get("core_validation_rate") or 0),
        "core_tested_hypotheses": int(gate.get("core_tested_hypotheses") or 0),
        "validation_rate": float(gate.get("validation_rate") or 0),
        "tested_hypotheses": int(gate.get("tested_hypotheses") or 0),
        "current_pattern": pattern.get("name"),
        "current_pattern_verdict": current_brain.get("verdict") or "样本不足，仅观察",
        "scenario": scenario.get("name"),
        "scenario_direction": scenario.get("direction"),
        "scenario_probability": float(scenario.get("probability") or 0),
    }


def _rank_score(gate: dict[str, Any]) -> float:
    core_rate = float(gate.get("core_validation_rate") or 0)
    all_rate = float(gate.get("validation_rate") or 0)
    core_tested = int(gate.get("core_tested_hypotheses") or 0)
    tested = int(gate.get("tested_hypotheses") or 0)
    core_patterns = int(gate.get("core_patterns") or gate.get("verified_patterns") or 0)
    if gate.get("projection_ready"):
        return (
            core_rate * 70
            + min(core_tested / 40, 1) * 20
            + min(core_patterns / 3, 1) * 10
        )
    if core_patterns:
        return (
            core_rate * 45
            + min(core_tested / 40, 1) * 15
            + min(core_patterns / 3, 1) * 5
        )
    return all_rate * 25 + min(tested / 100, 1) * 5


def _rank_sort_key(row: dict[str, Any]) -> tuple:
    return (
        bool(row["projection_ready"]),
        int(row["core_patterns"]) > 0,
        float(row["core_validation_rate"]),
        int(row["core_tested_hypotheses"]),
        float(row["validation_rate"]),
        int(row["tested_hypotheses"]),
        float(row["rank_score"]),
    )


def fetch_deep_news(inst_id: str, limit: int = 12) -> list[NewsItem]:
    return YahooNewsClient().latest_news(inst_id, limit=limit)


def _history(forecast: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in forecast.get("history", []) or []:
        try:
            close = float(item["close"])
            rows.append(
                {
                    "date": str(item["date"]),
                    "open": float(item.get("open", close)),
                    "high": float(item.get("high", close)),
                    "low": float(item.get("low", close)),
                    "close": close,
                    "volume": float(item.get("volume", 0) or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda row: row["date"])
    return rows


def _facts(
    item: dict[str, Any],
    prev: dict[str, Any],
    prior: list[dict[str, Any]],
) -> dict[str, Any]:
    close = float(item["close"])
    previous_close = float(prev["close"])
    ret = close / previous_close - 1 if previous_close > 0 else 0.0
    body = close / float(item["open"]) - 1 if item["open"] > 0 else 0.0
    gap = float(item["open"]) / previous_close - 1 if previous_close > 0 else 0.0
    day_range = float(item["high"]) / float(item["low"]) - 1 if item["low"] > 0 else 0.0
    volume_base = _avg([row["volume"] for row in prior[-20:]]) or item["volume"] or 1.0
    volume_ratio = float(item["volume"]) / volume_base if volume_base > 0 else 1.0
    high_20 = max(row["high"] for row in prior[-20:]) if len(prior) >= 20 else item["high"]
    low_20 = min(row["low"] for row in prior[-20:]) if len(prior) >= 20 else item["low"]
    ma20 = _avg([row["close"] for row in prior[-20:]])
    ma60 = _avg([row["close"] for row in prior[-60:]])
    upper_wick = (float(item["high"]) - max(float(item["open"]), close)) / close if close else 0.0
    lower_wick = (min(float(item["open"]), close) - float(item["low"])) / close if close else 0.0
    return {
        "return_pct": round(ret, 4),
        "body_pct": round(body, 4),
        "gap_pct": round(gap, 4),
        "range_pct": round(day_range, 4),
        "volume_ratio_20d": round(volume_ratio, 3),
        "breakout_20d": close > high_20,
        "breakdown_20d": close < low_20,
        "ma20_distance": round(close / ma20 - 1, 4) if ma20 else 0.0,
        "ma60_distance": round(close / ma60 - 1, 4) if ma60 else 0.0,
        "upper_wick_pct": round(upper_wick, 4),
        "lower_wick_pct": round(lower_wick, 4),
    }


def _pattern(facts: dict[str, Any]) -> dict[str, Any]:
    ret = facts["return_pct"]
    volume = facts["volume_ratio_20d"]
    if facts["breakout_20d"] and volume >= 1.35 and ret > 0:
        return _pattern_row(
            "volume_breakout",
            "放量突破",
            "up",
            ["收盘突破 20 日高点", f"成交量为 20 日均量 {volume:.2f} 倍"],
            "跌回突破日前收盘价或次日放量阴线",
        )
    if facts["breakdown_20d"] and volume >= 1.25 and ret < 0:
        return _pattern_row(
            "volume_breakdown",
            "放量跌破",
            "down",
            ["收盘跌破 20 日低点", f"成交量为 20 日均量 {volume:.2f} 倍"],
            "重新站回 20 日低点上方且缩量",
        )
    if facts["lower_wick_pct"] >= 0.025 and ret > -0.01:
        return _pattern_row(
            "lower_wick_reversal",
            "下影承接",
            "up",
            ["下影线明显", "收盘未延续日内低位"],
            "跌破当日低点",
        )
    if facts["upper_wick_pct"] >= 0.025 and ret < 0.015:
        return _pattern_row(
            "upper_wick_supply",
            "上影抛压",
            "down",
            ["上影线明显", "高位承接不足"],
            "放量收复当日高点",
        )
    if abs(ret) <= 0.012 and volume < 0.85:
        return _pattern_row(
            "quiet_compression",
            "缩量压缩",
            "neutral",
            ["涨跌幅收窄", f"成交量仅为均量 {volume:.2f} 倍"],
            "放量长实体突破压缩区间",
        )
    if facts["ma20_distance"] > 0 and ret >= 0:
        return _pattern_row(
            "trend_follow",
            "趋势延续",
            "up",
            ["收盘位于 20 日均线上方", "日收益为正"],
            "跌破 20 日均线",
        )
    return _pattern_row(
        "transition",
        "过渡震荡",
        "neutral",
        ["量价结构未形成明确优势"],
        "等待突破或跌破后重新判断",
    )


def _pattern_row(
    pattern_id: str,
    name: str,
    direction: str,
    evidence: list[str],
    invalidation: str,
) -> dict[str, Any]:
    return {
        "id": pattern_id,
        "name": name,
        "direction": direction,
        "evidence": evidence,
        "invalidation": invalidation,
    }


def _hypotheses(
    pattern: dict[str, Any],
    facts: dict[str, Any],
    events: list[NewsItem],
) -> list[dict[str, Any]]:
    event_evidence = [
        f"{event.source}: {event.title}"
        for event in events[:2]
    ]
    evidence = [
        *pattern["evidence"],
        f"日涨跌 {facts['return_pct'] * 100:.2f}%",
        f"量比 {facts['volume_ratio_20d']:.2f}",
        *event_evidence,
    ]
    confidence = 0.52
    confidence += 0.10 if facts["volume_ratio_20d"] >= 1.3 else 0
    confidence += 0.08 if events else 0
    confidence += 0.06 if pattern["id"] not in {"transition", "quiet_compression"} else 0
    confidence = min(0.86, confidence)
    return [
        {
            "claim": _claim(pattern, bool(events)),
            "direction": pattern["direction"],
            "confidence": round(confidence, 3),
            "evidence": evidence,
            "event_refs": [event.title for event in events[:2]],
        }
    ]


def _claim(pattern: dict[str, Any], has_events: bool) -> str:
    if has_events:
        return f"{pattern['name']} 叠加外部消息，可能解释当日走势。"
    return f"{pattern['name']} 主要由量价结构驱动，暂未匹配到同日外部消息。"


def _validate_hypothesis(
    candles: list[dict[str, Any]],
    index: int,
    direction: str,
) -> dict[str, Any]:
    if index + 5 >= len(candles):
        return {"status": "pending", "reason": "等待 5 个交易日后验证"}
    close = float(candles[index]["close"])
    future = candles[index + 1:index + 6]
    close_1d = future[0]["close"] / close - 1
    close_3d = future[min(2, len(future) - 1)]["close"] / close - 1
    close_5d = future[-1]["close"] / close - 1
    lows = [row["low"] / close - 1 for row in future]
    if direction == "up":
        confirmed = close_5d > 0 and close_3d > -0.015
    elif direction == "down":
        confirmed = close_5d < 0 and close_3d < 0.015
    else:
        confirmed = abs(close_5d) <= 0.025
    return {
        "status": "confirmed" if confirmed else "rejected",
        "return_1d": round(close_1d, 4),
        "return_3d": round(close_3d, 4),
        "return_5d": round(close_5d, 4),
        "max_drawdown_5d": round(min(lows), 4),
        "reason": "后续走势符合推测方向" if confirmed else "后续走势未验证该推测",
    }


def _pattern_verdict(samples: int, success_rate: float) -> str:
    tier = _pattern_quality_tier(samples, success_rate)
    if tier == "insufficient":
        return "样本不足，仅观察"
    if tier == "core":
        return "核心优势模式"
    if tier == "watch":
        return "弱优势模式"
    return "暂不进入核心大脑"


def _pattern_quality_tier(samples: int, success_rate: float) -> str:
    if samples < WATCH_MIN_SAMPLES:
        return "insufficient"
    if samples >= CORE_MIN_SAMPLES and success_rate >= CORE_MIN_SUCCESS_RATE:
        return "core"
    if samples >= WATCH_MIN_SAMPLES and success_rate >= WATCH_MIN_SUCCESS_RATE:
        return "watch"
    return "rejected"


def _low_confidence_scenarios(
    latest: dict[str, Any],
    current_pattern: dict[str, Any] | None,
    expected_return: float,
) -> list[dict[str, Any]]:
    pattern_name = latest["pattern"]["name"]
    if current_pattern:
        reason = (
            f"当前模式 {pattern_name} 样本 {current_pattern.get('samples', 0)}，"
            f"历史成功率约 {float(current_pattern.get('success_rate') or 0) * 100:.0f}%，"
            "未通过核心门槛，系统不把它作为主动推演依据。"
        )
    else:
        reason = (
            f"当前模式 {pattern_name} 尚无足够已验证样本，"
            "未通过核心门槛，系统不把它作为主动推演依据。"
        )
    conditional = max(0.12, min(0.28, 0.18 + max(expected_return, 0) * 0.8))
    risk = max(0.18, min(0.34, 0.20 + max(-expected_return, 0) * 1.2))
    base = max(0.38, 1 - conditional - risk)
    total = base + conditional + risk
    return [
        {
            "name": "基础情景",
            "probability": round(base / total, 4),
            "direction": "低可信观望",
            "reason": reason,
            "trigger": latest["pattern"]["invalidation"],
        },
        {
            "name": "条件情景",
            "probability": round(conditional / total, 4),
            "direction": "等待确认后再上修",
            "reason": "只有重新出现核心优势模式，或放量突破后不跌回关键位，才进入主动推演。",
            "trigger": "出现核心优势模式，且次日仍维持该结构。",
        },
        {
            "name": "风险情景",
            "probability": round(risk / total, 4),
            "direction": "误判延续 / 下探",
            "reason": "低质量模式下价格更容易否定推测，优先尊重失效条件。",
            "trigger": latest["pattern"]["invalidation"],
        },
    ]


def _news_by_day(news: list[NewsItem]) -> dict[str, list[NewsItem]]:
    rows: dict[str, list[NewsItem]] = defaultdict(list)
    for item in news:
        rows[item.published_at.date().isoformat()].append(item)
    return rows


def _sentiment(title: str) -> str:
    text = title.lower()
    positive = ("beat", "surge", "record", "upgrade", "growth", "approval", "profit")
    negative = ("miss", "probe", "lawsuit", "downgrade", "drop", "warning", "loss")
    if any(word in text for word in positive):
        return "positive"
    if any(word in text for word in negative):
        return "negative"
    return "neutral"


def _news_query_symbol(inst_id: str) -> str:
    symbol = inst_id.split("-")[0].strip().upper()
    if symbol in {"BTC", "ETH", "SOL", "XRP", "DOGE"}:
        return symbol + "-USD"
    return symbol


def _avg(values: list[float]) -> float:
    rows = [float(value) for value in values if value is not None]
    return sum(rows) / len(rows) if rows else 0.0


def _median(values: list[float]) -> float:
    rows = sorted(values)
    if not rows:
        return 0.0
    mid = len(rows) // 2
    if len(rows) % 2:
        return rows[mid]
    return (rows[mid - 1] + rows[mid]) / 2
