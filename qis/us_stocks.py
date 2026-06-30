from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
import urllib.parse
import urllib.request

from qis.models import Candle


class UsStockError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsStockHistory:
    symbol: str
    inst_id: str
    exchange: str
    currency: str
    quote_source: str
    trade_platform: str
    candles: list[Candle]


class YahooFinanceClient:
    BASE_URL = "https://query1.finance.yahoo.com"

    def daily_history(self, symbol: str, range_: str = "18mo") -> UsStockHistory:
        symbol = symbol.strip().upper()
        if not symbol:
            raise UsStockError("empty stock symbol")
        payload = self._request_json(
            "/v8/finance/chart/" + urllib.parse.quote(symbol),
            {
                "range": range_,
                "interval": "1d",
                "includePrePost": "false",
            },
        )
        chart = payload.get("chart") or {}
        error = chart.get("error")
        if error:
            raise UsStockError(str(error.get("description") or error))
        results = chart.get("result") or []
        if not results:
            raise UsStockError(f"no Yahoo Finance data for {symbol}")
        result = results[0]
        meta = result.get("meta") or {}
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        timestamps = result.get("timestamp") or []
        candles = []
        for index, ts in enumerate(timestamps):
            try:
                open_ = quote["open"][index]
                high = quote["high"][index]
                low = quote["low"][index]
                close = quote["close"][index]
                volume = quote.get("volume", [0] * len(timestamps))[index] or 0
            except (KeyError, IndexError, TypeError):
                continue
            if None in (open_, high, low, close):
                continue
            candles.append(
                Candle(
                    ts=datetime.fromtimestamp(int(ts), tz=timezone.utc),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=float(volume),
                )
            )
        if len(candles) < 90:
            raise UsStockError(f"not enough Yahoo Finance daily candles for {symbol}")
        exchange = str(meta.get("exchangeName") or meta.get("fullExchangeName") or "US")
        currency = str(meta.get("currency") or "USD")
        return UsStockHistory(
            symbol=symbol,
            inst_id=f"{symbol}-US",
            exchange=exchange,
            currency=currency,
            quote_source=f"Yahoo Finance 日线 · {exchange}",
            trade_platform="美股券商（IBKR / 富途 / 老虎 / Schwab 等）",
            candles=sorted(candles, key=lambda item: item.ts),
        )

    def _request_json(self, path: str, params: dict[str, str]) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}{path}?{query}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "qis-market-terminal/0.1"},
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return json.loads(response.read().decode())
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
        raise UsStockError(f"Yahoo Finance request failed: {last_error}") from last_error
