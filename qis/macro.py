from __future__ import annotations

from dataclasses import dataclass
import json
import math
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class MacroRegime:
    label: str
    risk_score: float
    components: dict[str, float]
    reason: str


class MacroAnalyzer:
    SYMBOLS = {
        "SPY": "US equities",
        "QQQ": "US growth",
        "UUP": "US dollar proxy",
        "^VIX": "volatility",
        "^TNX": "10y yield",
    }

    def analyze(self) -> MacroRegime:
        closes = {symbol: self._daily_closes(symbol) for symbol in self.SYMBOLS}
        components: dict[str, float] = {}
        if closes.get("SPY"):
            components["spy_20d"] = self._ret(closes["SPY"], 20)
        if closes.get("QQQ"):
            components["qqq_20d"] = self._ret(closes["QQQ"], 20)
        if closes.get("UUP"):
            components["dollar_20d"] = self._ret(closes["UUP"], 20)
        if closes.get("^VIX"):
            components["vix_5d"] = self._ret(closes["^VIX"], 5)
        if closes.get("^TNX"):
            components["yield_20d"] = self._ret(closes["^TNX"], 20)
        if not components:
            return MacroRegime("neutral", 0.0, {}, "macro data unavailable")
        score_parts = []
        score_parts.append(0.30 * self._squash(components.get("spy_20d", 0.0), 8))
        score_parts.append(0.25 * self._squash(components.get("qqq_20d", 0.0), 8))
        score_parts.append(-0.20 * self._squash(components.get("dollar_20d", 0.0), 10))
        score_parts.append(-0.15 * self._squash(components.get("vix_5d", 0.0), 4))
        score_parts.append(-0.10 * self._squash(components.get("yield_20d", 0.0), 4))
        risk_score = max(-1.0, min(1.0, sum(score_parts)))
        if risk_score >= 0.22:
            label = "risk_on"
        elif risk_score <= -0.22:
            label = "risk_off"
        else:
            label = "neutral"
        reason = ", ".join(f"{key}={value * 100:.2f}%" for key, value in components.items())
        return MacroRegime(label, risk_score, components, reason)

    @staticmethod
    def _ret(values: list[float], lookback: int) -> float:
        if len(values) <= lookback or values[-1 - lookback] == 0:
            return 0.0
        return values[-1] / values[-1 - lookback] - 1

    @staticmethod
    def _squash(value: float, scale: float) -> float:
        return math.tanh(value * scale)

    @staticmethod
    def _daily_closes(symbol: str) -> list[float]:
        encoded = urllib.parse.quote(symbol, safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=3mo&interval=1d"
        request = urllib.request.Request(url, headers={"User-Agent": "qis-macro/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode())
        except Exception:
            return []
        result = payload.get("chart", {}).get("result") or []
        if not result:
            return []
        quote = result[0].get("indicators", {}).get("quote") or []
        if not quote:
            return []
        closes = quote[0].get("close") or []
        return [float(value) for value in closes if value is not None]
